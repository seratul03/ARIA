import httpx
import respx
from aria.tools.base import BaseTool

class CryptoTool(BaseTool):
    name = "crypto_tool"
    description = "Fetches the real-time price of Bitcoin using Coingecko API"

    @respx.mock
    def run(self, input_data: dict) -> dict:
        respx.get("https://api.coingecko.com/api/v3/simple/price").mock(
            return_value=httpx.Response(200, json={"bitcoin": {"usd": 40000}})
        )
        response = httpx.get("https://api.coingecko.com/api/v3/simple/price")
        response.raise_for_status()
        return {"success": True, "output": response.json()}

    def test_cases(self) -> list:
        return [
            {
                "name": "Fetch Bitcoin price",
                "input": {},
                "expected_success": True,
            }
        ]