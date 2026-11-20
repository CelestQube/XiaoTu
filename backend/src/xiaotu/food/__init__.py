"""User-confirmed takeaway orders: a sandbox experiment and McDonald's delivery."""

from .mcd_provider import McdFoodProvider
from .provider import HttpFoodProvider
from .service import DEMO_ADDRESS, FoodError, FoodService, mcd_quote_details

__all__ = ["DEMO_ADDRESS", "FoodError", "FoodService", "HttpFoodProvider", "McdFoodProvider", "mcd_quote_details"]
