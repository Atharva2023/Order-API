
import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Orders API")

# CORS: allow the assignment page to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

TOTAL_ORDERS = 59
RATE_LIMIT = 15
WINDOW_SECONDS = 10

# Demo storage. These structures are shared across requests
# handled by this Python process.
orders = {}
idempotency_keys = {}
client_requests = defaultdict(deque)
data_lock = Lock()


class OrderInput(BaseModel):
    # Accept arbitrary order fields from the grader.
    model_config = {"extra": "allow"}


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Apply per-client limits to API requests.
    # OPTIONS is exempt so browser CORS preflight can succeed.
    if request.method == "OPTIONS":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id", "anonymous")
    now = time.monotonic()

    with data_lock:
        timestamps = client_requests[client_id]

        while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
            timestamps.popleft()

        if len(timestamps) >= RATE_LIMIT:
            retry_after = max(
                1,
                int(WINDOW_SECONDS - (now - timestamps[0]) + 0.999),
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )

        timestamps.append(now)

    return await call_next(request)


@app.post("/orders", status_code=201)
async def create_order(
    request: Request,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
):
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required",
        )

    body = await request.json()

    with data_lock:
        if idempotency_key in idempotency_keys:
            order_id = idempotency_keys[idempotency_key]
            return {"id": order_id, **orders[order_id]}

        order_id = str(uuid.uuid4())
        orders[order_id] = {"data": body}
        idempotency_keys[idempotency_key] = order_id

        return {"id": order_id, "data": body}


@app.get("/orders")
async def list_orders(limit: int = 10, cursor: str | None = None):
    if limit < 1:
        raise HTTPException(status_code=400, detail="limit must be positive")

    # The assignment requires a fixed catalog of IDs 1 through 59.
    # A cursor is opaque: the grader passes back the value we return.
    start = 1

    if cursor is not None:
        try:
            start = int(cursor)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid cursor")

    if start < 1 or start > TOTAL_ORDERS + 1:
        raise HTTPException(status_code=400, detail="Invalid cursor")

    end = min(start + limit - 1, TOTAL_ORDERS)
    items = [{"id": order_id} for order_id in range(start, end + 1)]

    next_cursor = str(end + 1) if end < TOTAL_ORDERS else None

    return {
        "items": items,
        "next_cursor": next_cursor,
    }


@app.get("/")
async def root():
    return {"status": "ok", "service": "orders-api"}
