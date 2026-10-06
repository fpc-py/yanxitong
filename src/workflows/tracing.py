"""会话级链路追踪状态（进程内）。

会话尚无独立 trace_id 字段，以 session_id 作为链路标识；
supervisor_graph 每次运行入口写入 LAST_TRACE_ID，
供 /api/system/capabilities 展示「最近一次运行」的追踪 ID。
"""

LAST_TRACE_ID: str | None = None
