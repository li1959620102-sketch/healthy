from pymilvus import MilvusClient, DataType

# 1. 初始化 Milvus 客户端
client = MilvusClient("tcp://localhost:19530")

# 2. 创建用于存储稀疏向量的集合
collection_name = "sparse_vectors"
schema = MilvusClient.create_schema(
    auto_id=True,
    enable_dynamic_field=True,
)
schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
schema.add_field(field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR)

index_params = client.prepare_index_params()
index_params.add_index(
    field_name="sparse_vector",
    index_type="SPARSE_INVERTED_INDEX",
    metric_type="IP",  # 修复：稀疏向量常用内积(IP)作为度量
    index_name="sparse_index",
    params={"inverted_index_algo": "DAAT_MAXSCORE"}
)

# 先删除旧集合
if client.has_collection(collection_name):
    client.drop_collection(collection_name)

client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params)
print(f"集合 {collection_name} 创建成功")

# 3. 插入稀疏向量数据
sparse_data = [
    {"sparse_vector": {1: 0.2, 50: 0.4, 1000: 0.7}},
    {"sparse_vector": {5: 0.1, 50: 0.3, 200: 0.9, 800: 0.6}},
    {"sparse_vector": {50: 0.6, 1000: 0.2, 1500: 0.5}},
    {"sparse_vector": {3: 0.8, 500: 0.4}},
]
insert_result = client.insert(collection_name=collection_name, data=sparse_data)
print(f"成功插入 {insert_result['insert_count']} 条稀疏向量数据")
client.flush(collection_name=collection_name)
# 4. 执行相似性搜索
query_sparse_vector = {50: 0.5, 1000: 0.8}

search_results = client.search(
    collection_name=collection_name,
    data=[query_sparse_vector],
    anns_field="sparse_vector",
    search_params={"params": {"drop_ratio_search": 0.0}},
    limit=5,
    output_fields=["id"]
)

print("\n最相似的5个结果（内积分数，值越高越相关）：")
for hits in search_results:
    for rank, hit in enumerate(hits, 1):
        print(f"  排名 {rank}: ID={hit['id']}, 内积分数={hit['distance']:.4f}")