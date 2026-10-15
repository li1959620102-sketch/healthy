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

# 配置信息
milvus_alias = "test_alias"
db_name = "test_database"
collection_name = "test_collection"
collection_alias = "test_collection_alias"
dim = 128  # 向量维度


def clear_database(db_name, using):
    """清空数据库中的所有集合"""
    # 切换到目标数据库
    db.using_database(db_name, using=using)
    # 获取数据库中所有集合
    collections = utility.list_collections(using=using)
    # 逐一删除集合
    for coll_name in collections:
        coll = Collection(coll_name, using=using)
        coll.drop()
        print(f"已删除集合: {coll_name}")
    return len(collections)


def main():
    try:
        # 1. 连接到Milvus服务
        connections.connect(
            alias=milvus_alias,
            host='localhost',
            port='19530'
        )
        print(f"成功连接到Milvus，连接别名: {milvus_alias}")

        # 2. 操作数据库
        # 检查数据库是否存在，存在则先清空再删除
        if db_name in db.list_database(using=milvus_alias):
            # 先清空数据库中的所有集合
            coll_count = clear_database(db_name, milvus_alias)
            print(f"已清空数据库 {db_name} 中的 {coll_count} 个集合")

            # 再删除数据库
            db.drop_database(db_name, using=milvus_alias)
            print(f"已删除数据库: {db_name}")

        # 创建新数据库
        db.create_database(db_name, using=milvus_alias)
        print(f"已创建数据库: {db_name}")

        # 切换到创建的数据库
        db.using_database(db_name, using=milvus_alias)
        print(f"已切换到数据库: {db_name}")

        # 3. 操作集合
        if collection_name in utility.list_collections(using=milvus_alias):
            old_collection = Collection(collection_name, using=milvus_alias)
            old_collection.drop()
            print(f"已删除现有集合: {collection_name}")

        # 定义集合结构
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema(name="category", dtype=DataType.INT64),
            FieldSchema(name="name", dtype=DataType.VARCHAR, max_length=100)
        ]

        schema = CollectionSchema(fields, description="测试集合")

        # 创建集合
        collection = Collection(
            name=collection_name,
            schema=schema,
            using=milvus_alias
        )
        print(f"已创建集合: {collection_name}")

        # 4. 插入数据
        num_entities = 30
        vectors = [np.random.rand(dim).astype(np.float32).tolist() for _ in range(num_entities)]
        categories = [random.randint(1, 5) for _ in range(num_entities)]
        names = [f"item_{i}" for i in range(num_entities)]

        insert_data = [vectors, categories, names]
        insert_result = collection.insert(insert_data)
        collection.flush()
        print(f"已插入 {len(insert_result.primary_keys)} 条数据")

        # 5. 创建索引
        index_params = {
            "index_type": "IVF_FLAT",
            "metric_type": "L2",
            "params": {"nlist": 100}
        }
        collection.create_index("vector", index_params)
        print("已创建向量索引")

        # 6. 加载集合
        collection.load()
        print("已加载集合到内存")

        # 7. 创建集合别名
        all_aliases = utility.list_aliases(collection_name, using=milvus_alias)
        alias_exists = any(alias["alias_name"] == collection_alias for alias in all_aliases)

        if alias_exists:
            utility.drop_alias(collection_alias, using=milvus_alias)
            print(f"已删除现有别名: {collection_alias}")

        utility.create_alias(collection_name, collection_alias, using=milvus_alias)
        print(f"已为集合创建别名: {collection_alias}")

        # 8. 向量搜索
        search_vector = np.random.rand(dim).astype(np.float32).tolist()
        search_params = {"metric_type": "L2", "params": {"nprobe": 10}}

        print("\n=== 向量搜索结果 ===")
        results = collection.search(
            data=[search_vector],
            anns_field="vector",
            param=search_params,
            limit=3,
            output_fields=["category", "name"]
        )

        for hits in results:
            for idx, hit in enumerate(hits, 1):
                print(f"排名 {idx}: ID={hit.id}, 距离={hit.distance:.4f}, "
                      f"类别={hit.entity['category']}, 名称={hit.entity['name']}")

        # 9. 标量查询
        print("\n=== 标量查询结果 (category=3) ===")
        query_expr = "category == 3"
        query_results = collection.query(
            expr=query_expr,
            output_fields=["id", "category", "name"],
            limit=5
        )

        for item in query_results:
            print(f"ID={item['id']}, 类别={item['category']}, 名称={item['name']}")

        # 10. 修改数据
        print("\n=== 修改数据 ===")
        update_expr = "category == 2"
        items_to_update = collection.query(
            expr=update_expr,
            output_fields=["id", "vector", "name"]
        )

        if items_to_update:
            ids_to_delete = [item["id"] for item in items_to_update]
            delete_result = collection.delete(f"id in {ids_to_delete}")
            print(f"已删除 {delete_result.delete_count} 条数据用于更新")

            new_vectors = [item["vector"] for item in items_to_update]
            new_names = [item["name"] for item in items_to_update]
            new_categories = [99 for _ in range(len(items_to_update))]

            update_data = [new_vectors, new_categories, new_names]
            update_result = collection.insert(update_data)
            collection.flush()
            print(f"已插入 {len(update_result.primary_keys)} 条更新后的数据")

        # 11. 删除数据
        print("\n=== 删除数据 ===")
        delete_expr = "category == 1"
        delete_result = collection.delete(delete_expr)
        collection.flush()
        print(f"已删除 {delete_result.delete_count} 条满足条件的数据")

        # 12. 释放集合
        collection.release()
        print("\n已释放集合内存")

        # 13. 删除集合别名
        utility.drop_alias(collection_alias, using=milvus_alias)
        print(f"已删除集合别名: {collection_alias}")

        # 14. 删除集合
        collection.drop()
        print(f"已删除集合: {collection_name}")

        # 15. 删除数据库（现在数据库应该为空了）
        db.drop_database(db_name, using=milvus_alias)
        print(f"已删除数据库: {db_name}")

    except Exception as e:
        print(f"操作出错: {str(e)}")
    finally:
        if connections.has_connection(milvus_alias):
            connections.disconnect(milvus_alias)
            print(f"已断开与Milvus的连接: {milvus_alias}")


if __name__ == "__main__":
    main()