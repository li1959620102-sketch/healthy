from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType
from pymilvus import AnnSearchRequest, WeightedRanker, RRFRanker
import random

# 1. 连接到 Milvus
connections.connect(host="127.0.0.1", port="19530")

# 2. 创建包含多个向量字段的 Collection Schema
fields = [
    FieldSchema(name="film_id", dtype=DataType.INT64, is_primary=True),
    FieldSchema(name="filmVector", dtype=DataType.FLOAT_VECTOR, dim=5),  # 电影向量字段
    FieldSchema(name="posterVector", dtype=DataType.FLOAT_VECTOR, dim=5),  # 海报向量字段
    FieldSchema(name="test1Vector", dtype=DataType.VARCHAR, max_length=20),  # test1向量字段
    FieldSchema(name="test2Vector", dtype=DataType.VARCHAR, max_length=20),  # test2向量字段
]

schema = CollectionSchema(fields=fields, enable_dynamic_field=False)
collection = Collection(name="test_collection", schema=schema)

# 3. 为每个向量字段创建索引
index_params = {
    "metric_type": "L2",
    "index_type": "IVF_FLAT",
    "params": {"nlist": 128},
}

collection.create_index("filmVector", index_params)
collection.create_index("posterVector", index_params)
# collection.create_index("test1Vector", index_params)
# collection.create_index("test2Vector", index_params)
collection.load()  # 将 Collection 加载到内存

# 4. 插入随机生成的数据（示例用）
num_entities = 1000
entities = []
for i in range(num_entities):
    entity = {
        "film_id": i,
        "filmVector": [random.random() for _ in range(5)],
        "posterVector": [random.random() for _ in range(5)],
        "test1Vector": f"test1Vector{i}",
        "test2Vector": f"test2Vector{i}",
    }
    entities.append(entity)

collection.insert(entities)
collection.flush()  # 确保数据持久化

# 5. 准备混合搜索
# 定义两个搜索请求 (AnnSearchRequest)，分别针对不同的向量字段
query_filmVector = [[0.8, 0.1, 0.5, 0.3, 0.9]]
search_param_1 = {
    "data": query_filmVector,
    "anns_field": "filmVector",
    "param": {"metric_type": "L2", "params": {"nprobe": 10}},
    "limit": 5  # 每个请求返回的结果数
}
request_1 = AnnSearchRequest(**search_param_1)

query_posterVector = [[0.2, 0.6, 0.4, 0.8, 0.1]]
search_param_2 = {
    "data": query_posterVector,
    "anns_field": "posterVector",
    "param": {"metric_type": "L2", "params": {"nprobe": 10}},
    "limit": 5
}

request_2 = AnnSearchRequest(**search_param_2)

# query_test1Vector = [[0.3, 0.5, 0.7, 0.5, 0.9]]
# search_param_3 = {
#     "data": query_test1Vector,
#     "anns_field": "test1Vector",
#     "param": {"metric_type": "L2", "params": {"nprobe": 10}},
#     "limit": 5  # 每个请求返回的结果数
# }
# request_3 = AnnSearchRequest(**search_param_3)
#
# query_test2Vector = [[0.2, 0.4, 0.6, 0.8, 0.1]]
# search_param_4 = {
#     "data": query_test2Vector,
#     "anns_field": "test2Vector",
#     "param": {"metric_type": "L2", "params": {"nprobe": 10}},
#     "limit": 5
# }

# request_4 = AnnSearchRequest(**search_param_4)

reqs = [request_1, request_2,
        # request_3, request_4,
        ]

# 6. 执行混合搜索并选择重排策略
# 策略一：加权评分 (WeightedRanker) - 为不同字段的结果分配权重
# weighted_ranker = WeightedRanker(0.7, 0.3)  # 两个权重值对应两个请求
# results = collection.hybrid_search(reqs, weighted_ranker, limit=5, output_fields=["film_id"])

# 策略二：互易等级融合 (RRFRanker) - 基于排名进行融合，无需指定权重
rrf_ranker = RRFRanker(k=60)  # k 为平滑参数，通常设为60
results = collection.hybrid_search(reqs, rrf_ranker, limit=5, output_fields=["film_id", "test1Vector", "test2Vector"])

# 7. 输出结果
print("混合搜索结果：")
for hits in results:
    for hit in hits:
        print(f"ID: {hit.entity.get('film_id')}, \n"
              f"Test1: {hit.entity.get('test1Vector')},\n"
              f"Test2: {hit.entity.get('test2Vector')},\n"
              f"距离: {hit.distance}\n")

# 8. 释放资源，集合清空
collection.release()
# 9. 删除集合
collection.drop()