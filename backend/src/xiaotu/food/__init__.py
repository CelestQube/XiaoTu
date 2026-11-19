"""User-confirmed, simulated takeaway orders. No live commerce integration."""

from .provider import HttpFoodProvider
from .service import DEMO_ADDRESS, FoodError, FoodService

__all__ = ["DEMO_ADDRESS", "FoodError", "FoodService", "HttpFoodProvider"]
