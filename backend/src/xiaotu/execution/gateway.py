from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from math import isfinite
from typing import TypeVar

T = TypeVar("T")
Trace = Callable[[str, int, str], Awaitable[None]]


class ToolRejected(Exception):
    """A definitive rejection reported by the tool (no write by this attempt)."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


class ToolUncertain(Exception):
    """The gateway does not know whether the operation was committed."""


class ToolSendLost(Exception):
    """The transport proved the request never reached the recipient.

    Resending the same logical request cannot create a second write, so a
    resend stays allowed even when the tool itself is not retry-safe.
    """

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ToolTransient(ToolUncertain):
    """A failure eligible for retry when the tool explicitly supports it."""

    def __init__(self, code: str, retry_after: float | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


@dataclass(frozen=True)
class ReliableExecutor:
    attempt_timeout: float = 0.8
    max_attempts: int = 3
    initial_backoff: float = 0.08
    maximum_backoff: float = 0.5

    async def call(
        self,
        invoke: Callable[[], Awaitable[T]],
        *,
        budget: float,
        retry_safe: bool,
        trace: Trace,
    ) -> T:
        """Bound transport sends by one budget. Never change the logical request.

        Callers designate a write retry-safe only when the recipient atomically
        deduplicates it. An unsuccessful *later* send cannot disprove a prior write.
        ToolSendLost is the exception: the transport proved the request never
        arrived, so a resend stays safe for any tool.
        """
        deadline = time.monotonic() + max(0, budget)
        ambiguous = False
        last_lost: ToolSendLost | None = None
        for number in range(1, self.max_attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await trace("attempt", number, "started")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            retry_after = None
            try:
                value = await asyncio.wait_for(invoke(), min(remaining, self.attempt_timeout))
            except ToolRejected as exc:
                await trace("response", number, exc.code)
                if not ambiguous:
                    raise
                raise ToolUncertain("earlier_attempt_unknown") from None
            except ToolSendLost as exc:
                # Definitively not delivered: a resend cannot create a second
                # write, so it stays allowed even for non-retry-safe tools.
                last_lost = exc
                await trace("response", number, exc.code)
            except ToolTransient as exc:
                ambiguous, retry_after = True, exc.retry_after
                await trace("response", number, exc.code)
            except (TimeoutError, asyncio.TimeoutError):
                ambiguous = True
                await trace("response", number, "timeout")
            except ToolUncertain:
                await trace("response", number, "invalid_or_unknown")
                raise
            except (OSError, ConnectionError):
                ambiguous = True
                await trace("response", number, "network")
            else:
                await trace("response", number, "ok")
                return value
            if (ambiguous and not retry_safe) or number >= self.max_attempts:
                break
            cap = min(self.maximum_backoff, self.initial_backoff * 2 ** (number - 1))
            delay = random.uniform(0, cap)
            if retry_after is not None and isfinite(retry_after):
                delay = max(delay, retry_after)
            if delay >= deadline - time.monotonic():
                break
            await trace("retry", number, "scheduled")
            await asyncio.sleep(delay)
        if last_lost is not None and not ambiguous:
            # Every send provably failed to leave; nothing was committed.
            raise ToolSendLost("send_deadline_exhausted") from None
        raise ToolUncertain("retry_or_deadline_exhausted")
