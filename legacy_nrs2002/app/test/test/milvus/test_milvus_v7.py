from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType
import random

# 固定随机种子，确保结果可复现
random.seed(42)

# 1. 连接 Milvus
connections.connect(host='127.0.0.1', port='19530')

# 2. 清理旧集合
collection_name = "unified_collection"
try:
    Collection(collection_name).drop()
    print(f"已删除旧集合 {collection_name}")
except Exception:
    pass

# 3. 创建新集合
dim = 128
schema = CollectionSchema(fields=[
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True),
    FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
    FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=50)
])
collection = Collection(name=collection_name, schema=schema)
print(f"已创建新集合 {collection_name}")

# 4. 创建分区并显式获取分区对象（旧版本兼容）
partition_image = collection.create_partition(partition_name="image_partition", partition_tag="image")
partition_text = collection.create_partition(partition_name="text_partition", partition_tag="text")
print(f"已创建分区: {partition_image.name}, {partition_text.name}")

# 5. 插入数据（增强向量关联性，确保跨分区搜索有结果）
def generate_related_vectors(base, count, noise=0.05):  # 降低噪声，增强关联性
    vectors = []
    for _ in range(count):
        vec = [x + random.uniform(-noise, noise) for x in base]
        vectors.append(vec)
    return vectors

# 生成关联更强的基础向量（让image和text向量有部分重叠）
base_common = [random.random() for _ in range(dim//2)]  # 公共基础部分（前64维）
base_image_unique = [random.random() for _ in range(dim//2)]  # 图像独有部分
base_text_unique = [random.random() for _ in range(dim//2)]   # 文本独有部分

# 合并基础向量（公共部分+独有部分，确保跨分区有一定相似度）
base_image_vector = base_common + base_image_unique
base_text_vector = base_common + base_text_unique

# 插入image分区数据
image_data = [
    [i for i in range(1000, 1010)],
    generate_related_vectors(base_image_vector, 10),
    ["image"] * 10
]
image_insert = partition_image.insert(image_data)  # 直接用分区对象插入（旧版本更可靠）

# 插入text分区数据
text_data = [
    [i for i in range(2000, 2010)],
    generate_related_vectors(base_text_vector, 10),
    ["text"] * 10
]
text_insert = partition_text.insert(text_data)  # 直接用分区对象插入

collection.flush()

# 验证分区数据量（确保text分区有数据）
try:
    print(f"image分区数据量: {partition_image.num_entities} 条")
    print(f"text分区数据量: {partition_text.num_entities} 条")
    print(f"总数据量: {collection.num_entities} 条（预期20条）")
except AttributeError:
    image_count = len(image_insert.primary_keys)
    text_count = len(text_insert.primary_keys)
    print(f"image分区数据量: {image_count} 条, text分区数据量: {text_count} 条")

# 6. 创建索引并加载所有分区（关键修复）
index_params = {"index_type": "FLAT", "metric_type": "L2", "params": {}}
collection.create_index(field_name="vector", index_params=index_params)
collection.load(["image_partition", "text_partition"])  # 显式加载两个分区
print("索引创建完成，已加载所有分区")

# 7. 执行搜索
# 7.1 搜索image分区（验证单分区）
query_image_vector = [base_image_vector[i] + random.uniform(-0.05, 0.05) for i in range(dim)]
results_image = collection.search(
    data=[query_image_vector],
    anns_field="vector",
    param={"metric_type": "L2", "params": {}},
    limit=5,
    partition_tags=["image"],
    output_fields=["id", "category"]
)
print("\n在 'image' 分区中的搜索结果:")
for hit in results_image[0]:
    print(f"ID: {hit.entity.get('id')}, Category: {hit.entity.get('category')}, 距离: {hit.distance:.4f}")

# 7.2 合并搜索（使用覆盖公共部分的查询向量，确保能匹配两个分区）
query_combined_vector = [
    base_common[i] + random.uniform(-0.05, 0.05)  # 公共部分添加噪声
    for i in range(dim//2)
] + [
    (base_image_unique[i] + base_text_unique[i])/2 + random.uniform(-0.05, 0.05)  # 混合独有部分
    for i in range(dim//2)
]

# 关键修复：不指定分区（搜索所有已加载分区）
results_combined = collection.search(
    data=[query_combined_vector],
    anns_field="vector",
    param={"metric_type": "L2", "params": {}},
    limit=10,  # 足够大的limit确保能返回结果
    output_fields=["id", "category"]  # 不指定分区，默认搜索所有加载的分区
)
print("\n在所有分区中的合并搜索结果:")
if results_combined and len(results_combined[0]) > 0:
    for hit in results_combined[0]:
        print(f"ID: {hit.entity.get('id')}, Category: {hit.entity.get('category')}, 距离: {hit.distance:.4f}")
else:
    print("  未找到匹配结果（请检查分区是否加载）")

# collection.release()
