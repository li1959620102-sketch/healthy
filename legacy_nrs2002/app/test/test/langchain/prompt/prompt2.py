from langchain_core.prompts import PromptTemplate

# # 定义多个变量的模版
# template = PromptTemplate(
# 	template="请评价{product}的优缺点，包括{aspect1}和{aspect2}。",
# 	input_variables=["product", "aspect1", "aspect2"]
# )
# # 使用模板生成提示词
# prompt_1 = template.format(
# 	product="智能手机",
# 	aspect1="电池续航",
# 	aspect2="拍照质量"
# )
#
# prompt_2 = template.format(
# 	product="笔记本电脑",
# 	aspect1="处理速度",
# 	aspect2="便携性"
# )
#
# print("提示词1:",prompt_1)
# print("提示词2:",prompt_2)
#
# # 方式2：调用from_template()
# prompt_template = PromptTemplate.from_template(
# 	"请给我一个关于{topic}的{type}解释。"
# )
# #传入模板中的变量名
# prompt = prompt_template.format(
# 	type="详细",
# 	topic="量子力学"
# )
# print(prompt)

# 部分提示词模版
# 在生成prompt前就已经提前初始化部分的提示词，实际进一步导入模版的时候只导入除已初始化的变量即可。
template2 = PromptTemplate(
    template = "{foo}{bar}",
    input_variables=["foo", "bar"],
    partial_variables={"foo": "Hello"}
)
 
print(template2.format(bar="world"))


# 使用 PromptTemplate.partial() 方法创建部分提示模板
template1 = PromptTemplate(
    template="{foo}{bar}",
    input_variables=["foo", "bar"]
)
#方式1：
partial_template1 = template1.partial(foo="hello")
prompt1 = partial_template1.format(bar="world")
print(prompt1)
# 案例1
# 完整的模版
full_template ="""
    你是一个{role}, 请用{style}风格回答：
    问题：{question}
    答案：
"""
# 预填充角色和风格
partial_template = PromptTemplate.from_template(full_template).partial(
    role="资深厨师",
    style="专业但幽默"
)
 
# 只需提供剩余变量
prompt = partial_template.format(question="如何煎牛排?")
print(prompt)

