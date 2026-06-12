import httpx
from aria.tools.base import BaseTool
import respx

class CryptoTool(BaseTool):
    name = "crypto_tool"
    description = "Fetches the real-time price of Bitcoin using Coingecko API"

    def run(self, input_data: dict) -> dict:
        """Fetches the real-time price of Bitcoin using Coingecko API"""
        try:
            response = httpx.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd")
            response.raise_for_status()
            return {"success": True, "output": response.json()}
        except httpx.HTTPError as e:
            return {"success": False, "output": str(e)}

    def mock_apis(self, respx_mock):
        """Mock Coingecko API for testing"""
        respx_mock.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd").mock(
            return_value=httpx.Response(200, json={"bitcoin": {"usd": 30000.0}})
        )

    def test_cases(self) -> list:
        """Returns a list of test cases for the tool"""
        return [
            {
                "name": "Test successful response",
                "input": {},
                "expected_success": True,
            }
        ]