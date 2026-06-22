import httpx
from aria.tools.base import BaseTool, TestCase, ToolResult
import respx

class CryptoTool(BaseTool):
    name = "crypto_tool"
    description = "Fetches the real-time price of Bitcoin using Coingecko API"

    def run(self, input_data: dict) -> ToolResult:
        """Fetches the real-time price of Bitcoin using Coingecko API"""
        try:
            response = httpx.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd")
            response.raise_for_status()
            return ToolResult(success=True, output=response.json(), error=None)
        except httpx.HTTPError as e:
            return ToolResult(success=False, output=None, error=str(e))

    def mock_apis(self, respx_mock):
        """Mock Coingecko API for testing"""
        respx_mock.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd").mock(
            return_value=httpx.Response(200, json={"bitcoin": {"usd": 30000.0}})
        )

    def test_cases(self) -> list[TestCase]:
        """Returns a list of test cases for the tool"""
        return [
            TestCase(
                name="successful_response",
                input={},
                expected_success=True,
            )
        ]