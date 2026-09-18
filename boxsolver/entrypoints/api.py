"""HTTP transport for BoxSolverAlgorithm.

Run it:

    uvicorn boxsolver.entrypoints.api:app --reload --port 8000

Then open http://localhost:8000/docs for interactive documentation you can
click through.

Endpoints
    GET  /health          liveness probe for uptime checks
    GET  /v1/orientations the six orientation codes and their Euler angles
    POST /v1/pack         the solve endpoint

Deliberately thin: all packing logic lives in :mod:`boxsolver.core.packer`.
This file only receives, validates, translates and returns, so the HTTP path
and any in-process caller can never disagree about results.

Error contract:
    200  a solution - but CHECK ``Success``. A partial solution with items in
         ``UnpackedItems`` is a legitimate result, not an error.
    422  the payload did not match the schema. ``Message`` names the offending
         index and field, and is safe to show an operator.
    500  an unexpected engine fault. Never an HTML traceback - always JSON.

Configuration (environment variables):
    BOXSOLVER_CORS_ORIGINS   comma-separated allowed origins.
                             Defaults to "*" for local development.
                             Set to the caller's deployed origin in production.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "boxsolver.entrypoints.api needs FastAPI: pip install 'fastapi[standard]' uvicorn"
    ) from exc

from ..core.geometry import ORIENTATIONS
from ..core.packer import solve
from ..models import PackRequest, SchemaError
from ..output import __version__

logger = logging.getLogger("boxsolver.api")


# --------------------------------------------------------------------------- #
# request models - a fast first filter before models.py does the real checking
# --------------------------------------------------------------------------- #
class BoxTypeModel(BaseModel):
    """Mirrors BoxType in packer_schemas.yaml."""

    Reference: str
    Width: float
    Length: float
    Depth: float
    MaxWeight: Optional[float] = None
    BoxWeight: Optional[float] = None
    Active: Optional[bool] = True
    MaximumBoxes: Optional[int] = None


class ItemModel(BaseModel):
    """Mirrors Item in packer_schemas.yaml, plus the agreed optional extensions."""

    ItemCode: str
    ItemReference: str
    Width: float
    Length: float
    Depth: float
    Weight: float
    BoxGroup: Optional[str] = None
    Quantity: Optional[int] = Field(default=1, ge=1)
    KeepUpright: Optional[bool] = False
    AllowedRotations: Optional[List[str]] = None


class OptionsModel(BaseModel):
    Objective: str = "min_boxes"
    AllowRotation: bool = True
    RequireSupport: bool = True
    MinSupportRatio: float = Field(default=0.50, ge=0.0, le=1.0)
    AllowUngroupedToShare: bool = True
    TimeLimitSeconds: float = Field(default=20.0, gt=0)


class PackRequestModel(BaseModel):
    Boxes: List[BoxTypeModel]
    Items: List[ItemModel]
    Options: Optional[OptionsModel] = None
    RequestId: Optional[str] = Field(
        default=None,
        description=(
            "Optional correlation ID. BoxSolverAlgorithm is stateless and does not store "
            "it - we simply echo it back in Meta.RequestId so callers can match "
            "a response to the order that produced it in their logs."
        ),
    )


app = FastAPI(
    title="BoxSolverAlgorithm API",
    version=__version__,
    description="Project Perfect Fit optimisation engine (3D bin packing).",
)


# --------------------------------------------------------------------------- #
# CORS - configurable, because "*" must not reach production
# --------------------------------------------------------------------------- #
_origins = os.environ.get("BOXSOLVER_CORS_ORIGINS", "*")
ALLOWED_ORIGINS = [o.strip() for o in _origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# error handling
# --------------------------------------------------------------------------- #
@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Give every 422 the same body shape.

    Without this there are TWO kinds of validation failure with two different
    bodies, which is a trap for the caller:

      * FastAPI/pydantic catches shape problems first (a missing field, a string
        where a number belongs) and returns ``{"detail": [ {...}, {...} ]}`` -
        a LIST of machine-generated error objects.
      * Our own models.py catches semantic problems (a negative weight, a
        duplicate box Reference) and returns
        ``{"detail": {"Error": ..., "Message": ...}}`` - a DICT.

    A caller would have to branch on the type of ``detail`` to read either. This
    handler rewrites pydantic's list into our shape, so there is exactly one
    format to parse and one place to read the message from.
    """
    problems = []
    for error in exc.errors():
        # loc looks like ("body", "Items", 0, "Weight") - drop the leading
        # "body" and join the rest into something a human can act on.
        location = ".".join(str(part) for part in error["loc"][1:]) or "body"
        problems.append(f"{location}: {error['msg']}")
    return JSONResponse(
        status_code=422,
        content={
            "Error": "SCHEMA_ERROR",
            "Message": "; ".join(problems),
        },
    )



@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    """Unwrap our own HTTPExceptions so the body is flat, matching the above."""
    detail = exc.detail
    if isinstance(detail, dict):
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"Error": "ERROR", "Message": str(detail)},
    )


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Last line of defence.

    Any bug in the engine would otherwise surface as an HTML error page, which
    a caller's JSON parser would choke on. This turns it into a predictable body
    they can handle, and logs the real traceback on our side.
    """
    logger.exception("unhandled error solving %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "Error": "INTERNAL_ERROR",
            "Message": "The solver failed unexpectedly. This has been logged.",
        },
    )


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
def health() -> Dict[str, Any]:
    """Liveness probe. Callers poll this to check the service is up."""
    return {"status": "ok", "engine": "boxsolver", "version": __version__}


@app.get("/v1/orientations")
def orientations() -> Dict[str, Any]:
    """Reference data so a renderer can build a rotation lookup at startup."""
    return {"Orientations": [o.to_dict() for o in ORIENTATIONS]}


@app.post("/v1/pack")
def pack(request: PackRequestModel) -> Dict[str, Any]:
    """Pack items into cartons.

    Returns 200 with a solution. Note that 200 does NOT mean everything was
    packed - callers must check ``Success`` and ``UnpackedItems``.
    """
    payload = request.model_dump(exclude_none=True)
    payload.pop("RequestId", None)  # not part of the engine's input contract

    try:
        solution = solve(PackRequest.from_dict(payload))
    except SchemaError as exc:
        # Malformed payload. The message names the field, e.g.
        # "Items[2] (ITM-003): required field 'Weight' is missing".
        # Same body shape as the pydantic handler above - see validation_error.
        raise HTTPException(
            status_code=422,
            detail={"Error": "SCHEMA_ERROR", "Message": str(exc)},
        ) from exc

    document = solution.to_dict()
    if request.RequestId is not None:
        # Additive only - safe within SchemaVersion 1.x.
        document["Meta"]["RequestId"] = request.RequestId
    return document


def run() -> None:  # pragma: no cover
    """Console-script entry point: ``boxsolver-api``.

    Equivalent to ``uvicorn boxsolver.entrypoints.api:app``. Host and port come
    from BOXSOLVER_HOST / BOXSOLVER_PORT so deployment does not need a code
    change.
    """
    import uvicorn

    uvicorn.run(
        "boxsolver.entrypoints.api:app",
        host=os.environ.get("BOXSOLVER_HOST", "127.0.0.1"),
        port=int(os.environ.get("BOXSOLVER_PORT", "8000")),
    )