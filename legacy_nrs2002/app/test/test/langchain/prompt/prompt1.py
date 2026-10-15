from langchain_core.prompts import PromptTemplate

# 定义模板：描述主题的应用
template = PromptTemplate(
	template="请简要描述{topic}的应用。",
	input_variables=["topic"]
)
print(template)

# 使用模板生成提示词
prompt_1 = template.format(topic="机器学习")
prompt_2 = template.format(topic="自然语言处理")

print("提示词1:", prompt_1)
print("提示词2:", prompt_2)

