class McpError(Exception):
    pass


class McpAuthError(McpError):
    """未授权、token 失效且无法续期。需要用户在设置页重新授权。"""


class McpBusinessError(McpError):
    """工具返回 ok=false，属于业务错误，不重试。"""


class McpTransportError(McpError):
    """网络、超时、服务端 5xx 等可重试错误在重试耗尽后抛出。"""


class McpRateLimited(McpTransportError):
    """腾讯返回"服务限频"，自动冷却重试多次后仍被限频。"""
