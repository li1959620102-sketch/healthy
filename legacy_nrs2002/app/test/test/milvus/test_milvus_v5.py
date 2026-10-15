from pymilvus import MilvusClient, DataType, Function, FunctionType
import time
import numpy as np

# 1. 初始化 Milvus 客户端
client = MilvusClient("tcp://localhost:19530")

# 2. 创建集合及准备数据
collection_name = "time_decay_demo"

if client.has_collection(collection_name):
    client.drop_collection(collection_name)

# 定义schema
schema = MilvusClient.create_schema(auto_id=True, enable_dynamic_field=True)
schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=128)
schema.add_field(field_name="timestamp", datatype=DataType.INT64)

# 创建索引
index_params = client.prepare_index_params()
index_params.add_index(field_name="embedding", index_type="FLAT", metric_type="IP")

client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params)
print(f"集合 {collection_name} 创建成功")

# 插入测试数据
num_vectors = 5
current_ts = int(time.time())
embeddings = np.random.rand(num_vectors, 128).tolist()
data = [
    {"embedding": embeddings[i], "timestamp": current_ts - i * 86400}
    for i in range(num_vectors)
]
insert_result = client.insert(collection_name=collection_name, data=data)
print(f"成功插入 {insert_result['insert_count']} 条测试数据")
client.flush(collection_name=collection_name)
# 3. 创建时间衰减函数
decay_ranker = Function(
    name="time_decay",
    input_field_names=["timestamp"],
    function_type=FunctionType.RERANK,
    params={
        "reranker": "decay",
        "function": "gauss",
        "origin": current_ts,
        "scale": 2 * 86400,
        "offset": 86400,
        "decay": 0.5
    }
)

# 4. 执行带时间衰减的搜索
query_vector = np.random.rand(128).tolist()
search_results = client.search(
    collection_name=collection_name,
    data=[query_vector],
    anns_field="embedding",
    search_params={"params": {"nprobe": 10}},
    limit=5,
    output_fields=["id", "timestamp"],
    rerank=decay_ranker
)

# 5. 打印结果（兼容'score'或'distance'字段）
print("\n应用时间衰减后的搜索结果：")
for hits in search_results:
    for rank, hit in enumerate(hits, 1):
        # 检查得分字段（不同版本可能用'score'或'distance'）
        score_key = "score" if "score" in hit else "distance"
        combined_score = hit[score_key]

        print(f"排名 {rank}:")
        print(f"  ID: {hit['id']}")
        print(f"  原始向量相似度: {hit['distance']:.4f}")  # 原始距离始终在'distance'
        print(
            f"  时间戳: {hit['entity']['timestamp']} (距今约 {int((current_ts - hit['entity']['timestamp']) / 86400)} 天)")
        print(f"  综合得分: {combined_score:.4f} (向量相似度 × 时间衰减因子)\n")