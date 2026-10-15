from datetime import datetime, date
import re
import logging

logger = logging.getLogger(__name__)

def extract_sql_after_escape_mark(input_text: str) -> str:
    """
    从输入文本中截取“</think>”标记之后的纯SQL语句
    功能：
    1. 自动定位最后一个“</think>”（避免多个标记干扰）
    2. 清理SQL前后的空白、换行、注释文本
    3. 处理边界情况（无标记、标记后无内容）

    :param input_text: 包含“</think>”和SQL的混合文本
    :return: 清理后的纯SQL语句（或提示信息）
    """
    # 1. 检查是否包含“</think>”标记
    pos = input_text.rfind("</think>")
    print(f"pos:{pos}")
    if pos != -1:
        # 2. 定位最后一个“</think>”的位置（优先取最后一段SQL）
        last_escape_index = input_text.rfind("</think>")
        # 截取标记之后的内容
        sql_candidate = input_text[last_escape_index + len("</think>"):]
        print(f"sql_candidate: {sql_candidate}")

        # 3. 清理SQL候选内容：去除前后空白、多余换行，过滤非SQL文本
        # （保留以SELECT/INSERT/UPDATE/DELETE开头的内容，排除后续注释）
        sql_candidate = sql_candidate.strip()  # 去除前后空白
        if not sql_candidate:
            return "错误：“</think>”标记之后无有效内容"

        # 4. 提取纯SQL（匹配以SQL关键字开头的语句，直到分号或文本结束）
        # 支持的SQL关键字：SELECT/INSERT/UPDATE/DELETE（不区分大小写）
        sql_pattern = r"(?i)^(SELECT|INSERT|UPDATE|DELETE)\s+.*?(?=;|$)"
        sql_match = re.search(sql_pattern, sql_candidate, re.DOTALL)

        if sql_match:
            pure_sql = sql_match.group().strip()
            # 补充SQL结尾分号（如果没有）
            if not pure_sql.endswith(";"):
                pure_sql += ";"
            return pure_sql.strip()
        else:
            return f"错误：“</think>”标记之后未识别到有效SQL（候选内容：{sql_candidate[:50]}...）"
    else:
        return input_text.strip()


def format_json(result: list):
    for item in result:
        # 确保item是字典（避免非字典元素报错）
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            # 处理datetime类型（含时间）
            if isinstance(value, datetime):
                item[key] = value.strftime("%Y-%m-%d %H:%M:%S")
            # 处理date类型（仅日期，可选）
            elif isinstance(value, date):
                item[key] = value.strftime("%Y-%m-%d")
            # 其他类型转为字符串
            else:
                item[key] = str(value)
    return result  # 注意缩进：应在循环外，确保所有元素都处理完
# ------------------------------
# 测试：模拟用户的错误输入文本
# ------------------------------
if __name__ == "__main__":
    # 模拟用户的混合文本（包含“</think>”、思考内容、SQL）
    user_input = """


SELECT user_name, age, total_score, risk_level FROM user_health_risk_assessment WHERE risk_level = '高风险' ORDER BY total_score DESC LIMIT 10

"""

    # 提取SQL并打印结果
    result_sql = extract_sql_after_escape_mark(user_input)
    print("提取到的纯SQL语句：")
    print(result_sql)