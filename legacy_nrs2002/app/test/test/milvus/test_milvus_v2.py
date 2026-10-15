from pymilvus import (
    connections,
    FieldSchema,
    CollectionSchema,
    DataType,
    Collection,
    db,
    utility
)
import numpy as np
import random

# 配置
milvus_alias = "similarity_demo"
db_name = "similarity_db"
collection_name = "similarity_collection"
dim = 64  # 向量维度


def setup_milvus():
    """初始化Milvus连接和环境"""
    # 连接服务
    connections.connect(
        alias=milvus_alias,
        host='localhost',
        port='19530'
    )

    # 清理旧环境
    if db_name in db.list_database(using=milvus_alias):
        db.using_database(db_name, using=milvus_alias)
        for coll in utility.list_collections(using=milvus_alias):
            Collection(coll, using=milvus_alias).drop()
        db.drop_database(db_name, using=milvus_alias)

    # 创建新数据库
    db.create_database(db_name, using=milvus_alias)
    db.using_database(db_name, using=milvus_alias)

    return True


def create_collection(metric_type):
    """创建指定相似度度量的集合"""
    # 定义字段
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
        FieldSchema(name="label", dtype=DataType.INT64),  # 用于验证匹配效果的标签
        FieldSchema(name="desc", dtype=DataType.VARCHAR, max_length=100)  # 向量描述
    ]

    # 创建集合
    schema = CollectionSchema(fields, description=f"使用{metric_type}的集合")
    collection = Collection(
        name=f"{collection_name}_{metric_type.lower()}",
        schema=schema,
        using=milvus_alias
    )

    # 创建索引（使用IVF_FLAT索引，适用于大多数场景）
    index_params = {
        "index_type": "IVF_FLAT",
        "metric_type": metric_type,
        "params": {"nlist": 100}
    }
    collection.create_index("vector", index_params)
    return collection


def insert_demo_data(collection, num_samples=50):
    """插入演示数据：包含相似组和随机组"""
    vectors = []
    labels = []
    descs = []

    # 生成3组相似向量（每组10个）
    for group_id in range(3):
        # 每组的基础向量
        base_vec = np.random.rand(dim).astype(np.float32)
        for i in range(10):
            # 在基础向量上添加小扰动，生成相似向量
            vec = base_vec + np.random.normal(0, 0.05, dim).astype(np.float32)
            vectors.append(vec.tolist())
            labels.append(group_id)
            descs.append(f"组{group_id}_样本{i}")

    # 生成20个随机向量（不相似）
    for i in range(20):
        vectors.append(np.random.rand(dim).astype(np.float32).tolist())
        labels.append(99)  # 用99表示随机组
        descs.append(f"随机样本{i}")

    # 插入数据
    insert_data = [vectors, labels, descs]
    collection.insert(insert_data)
    collection.flush()
    collection.load()
    print(f"已插入 {len(vectors)} 条数据（3组相似向量+随机向量）")
    return vectors


def search_and_compare(collection, query_vec, metric_type, top_k=5):
    """执行搜索并展示结果"""
    search_params = {
        "metric_type": metric_type,
        "params": {"nprobe": 10}
    }

    results = collection.search(
        data=[query_vec],
        anns_field="vector",
        param=search_params,
        limit=top_k,
        output_fields=["label", "desc"]
    )

    print(f"\n===== {metric_type} 搜索结果 =====")
    for hits in results:
        for idx, hit in enumerate(hits, 1):
            print(f"排名 {idx}: "
                  f"距离={hit.distance:.6f}, "
                  f"标签={hit.entity['label']}, "
                  f"描述={hit.entity['desc']}")
    return results


def main():
    # 初始化环境
    setup_milvus()
    print("Milvus环境初始化完成")

    # 生成查询向量（基于组0的基础向量稍作修改）
    base_vec = np.random.rand(dim).astype(np.float32)  # 模拟与组0相似的查询向量
    query_vec = (base_vec + np.random.normal(0, 0.03, dim).astype(np.float32)).tolist()
    # 分别使用三种相似度方法进行检索
    for metric in ["L2", "IP", "COSINE"]:
        # 创建集合
        coll = create_collection(metric)
        # 插入数据
        insert_demo_data(coll)
        # 执行搜索
        search_and_compare(coll, query_vec, metric)

        # 清理
        # coll.release()
        # coll.drop()

    # 断开连接
    connections.disconnect(milvus_alias)
    print("\n所有操作完成，已断开连接")
if __name__ == "__main__":
    main()