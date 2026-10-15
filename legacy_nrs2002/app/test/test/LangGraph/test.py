from typing import TypedDict, Literal
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI

# 步骤1：定义状态结构——全局共享的黑板
class AgentState(TypedDict):
    messages: list           # 对话历史
    next_step: str           # 下一步动作
    retry_count: int         # 重试计数
    searched: bool  # 标记是否已完成搜索（新增）


# 步骤2：定义节点函数——封装业务逻辑
def analyze_node(state: AgentState) -> dict:
    """分析节点：处理用户输入，决定下一步"""
    # 如果已经搜索过，直接进入响应
    if state.get("searched", False):
        return {"next_step": "respond", "retry_count": state["retry_count"]}

    last_msg = state["messages"][-1] if state["messages"] else ""
    if "查询" in last_msg:
        return {"next_step": "search", "retry_count": state["retry_count"]}
    else:
        return {"next_step": "respond", "retry_count": state["retry_count"]}

def search_node(state: AgentState) -> dict:
    """搜索节点：调用工具获取信息"""
    search_result = f"搜索结果为：{state['messages'][-1]}"
    return {
        "messages": state["messages"] + [search_result],
        "next_step": "analyze",  # 返回分析节点，但此时 searched 已为 True，会直接跳转响应
        "searched": True  # 标记已搜索
    }

def respond_node(state: AgentState) -> dict:
    """响应节点：生成最终回复"""
    response = "已为您找到相关信息"
    return {"messages": state["messages"] + [response]}

# 步骤3：构建图和边关系
builder = StateGraph(AgentState)

# 添加节点
builder.add_node("analyze", analyze_node)
builder.add_node("search", search_node)
builder.add_node("respond", respond_node)

# 添加边
builder.set_entry_point("analyze")          # 设置入口
builder.add_edge("search", "analyze")        # 无条件边：搜索后返回分析
builder.add_edge("respond", END)             # 响应后结束

# 条件边：根据状态决定路由
builder.add_conditional_edges(
    "analyze",
    lambda state: state["next_step"],        # 根据 next_step 值选择目标
    {
        "search": "search",
        "respond": "respond"
    }
)

# 步骤4：编译并执行
graph = builder.compile()
result = graph.invoke({"messages": ["帮我查询天气"], "next_step": "", "retry_count": 0,"searched":False})
print(result)