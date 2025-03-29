from pymilvus import model
from pymilvus import MilvusClient
import pandas as pd
from tqdm import tqdm
import logging
from dotenv import load_dotenv
load_dotenv()
import torch    
from pymilvus import MilvusClient, DataType, FieldSchema, CollectionSchema

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 初始化 OpenAI 嵌入函数
embedding_function = model.dense.SentenceTransformerEmbeddingFunction(
            # model_name='nvidia/NV-Embed-v2', 
            # model_name='dunzhang/stella_en_1.5B_v5',
            # model_name='all-mpnet-base-v2',
            model_name='intfloat/multilingual-e5-large-instruct',
            device='cuda:0' if torch.cuda.is_available() else 'cpu',
            trust_remote_code=True
        )
# embedding_function = model.dense.OpenAIEmbeddingFunction(model_name='text-embedding-3-small')

# 文件路径
file_path = "/home/huangj2/Documents/evyd-wp1/01.standardization/data/SNOMED-CT/SNOMED_valid_with_synonym_comma.csv"
# db_path = "./snomed_syn_mpnet-base-v2.db"
db_path = "/home/huangj2/Documents/evyd-wp1/backend/db/snomed_e5_large.db"

# 连接到 Milvus
client = MilvusClient(db_path)

# collection_name = "concepts_only_name"
collection_name = "concepts_with_synonym"

# 加载数据
logging.info("Loading data from CSV")
df = pd.read_csv(file_path, 
                #  delimiter='\t', 
                 dtype=str, 
                #  nrows=2
                 ).fillna("NA")

# 获取向量维度（使用一个样本文档）
sample_doc = "Sample Text"
sample_embedding = embedding_function([sample_doc])[0]
vector_dim = len(sample_embedding)

# 构造Schema
fields = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=vector_dim),
    FieldSchema(name="concept_id", dtype=DataType.VARCHAR, max_length=50),
    FieldSchema(name="concept_name", dtype=DataType.VARCHAR, max_length=200),
    FieldSchema(name="domain_id", dtype=DataType.VARCHAR, max_length=20),
    FieldSchema(name="vocabulary_id", dtype=DataType.VARCHAR, max_length=20),
    FieldSchema(name="concept_class_id", dtype=DataType.VARCHAR, max_length=20),
    FieldSchema(name="standard_concept", dtype=DataType.VARCHAR, max_length=1),
    FieldSchema(name="concept_code", dtype=DataType.VARCHAR, max_length=50),
    FieldSchema(name="valid_start_date", dtype=DataType.VARCHAR, max_length=10),
    FieldSchema(name="valid_end_date", dtype=DataType.VARCHAR, max_length=10),
    # FieldSchema(name="invalid_reason", dtype=DataType.VARCHAR, max_length=1),    
    # FieldSchema(name="full_name", dtype=DataType.VARCHAR, max_length=500),
    FieldSchema(name="synonyms", dtype=DataType.VARCHAR, max_length=1000),
    # FieldSchema(name="definitions", dtype=DataType.VARCHAR, max_length=1000),
    FieldSchema(name="input_file", dtype=DataType.VARCHAR, max_length=500),
]
schema = CollectionSchema(fields, 
                          "SNOMED-CT Concepts", 
                          enable_dynamic_field=True)

# 如果集合不存在，创建集合
if not client.has_collection(collection_name):
    client.create_collection(
        collection_name=collection_name,
        schema=schema,
        # dimension=vector_dim
    )
    logging.info(f"Created new collection: {collection_name}")

# # 在创建集合后添加索引
index_params = client.prepare_index_params()
index_params.add_index(
    field_name="vector",  # 指定要为哪个字段创建索引，这里是向量字段
    index_type="AUTOINDEX",  # 使用自动索引类型，Milvus会根据数据特性选择最佳索引
    metric_type="COSINE",  # 使用余弦相似度作为向量相似度度量方式
    params={"nlist": 1024}  # 索引参数：nlist表示聚类中心的数量，值越大检索精度越高但速度越慢
)

client.create_index(
    collection_name=collection_name,
    index_params=index_params
)

# 批量处理
batch_size = 1024

for start_idx in tqdm(range(0, len(df), batch_size), desc="Processing batches"):
    end_idx = min(start_idx + batch_size, len(df))
    batch_df = df.iloc[start_idx:end_idx]

    # 准备文档
    # docs = [f"Term: {row['concept_name']}; Synonyms: {row['Synonyms']}" for _, row in batch_df.iterrows()]
    docs = []
    for _, row in batch_df.iterrows():
        doc_parts = [row['concept_name']]

        # if row['Full Name'] != "NA" and row['Full Name'] != row['concept_name']:
        #     doc_parts.append(",Full Name: " + row['Full Name'])

        if row['Synonyms'] != "NA" and row['Synonyms'] != row['concept_name']:
            doc_parts.append(", Synonyms: " + row['Synonyms'])

        # if row['Definitions'] != "NA" and row['Definitions'] not in [row['concept_name'], row.get('Full Name', '')]:
        #     doc_parts.append(", Definitions: " + row['Definitions'])

        docs.append(" ".join(doc_parts))


    # 生成嵌入
    try:
        embeddings = embedding_function(docs)
        logging.info(f"Generated embeddings for batch {start_idx // batch_size + 1}")
    except Exception as e:
        logging.error(f"Error generating embeddings for batch {start_idx // batch_size + 1}: {e}")
        continue

    # 准备数据
    data = [
        {
            # "id": idx + start_idx,
            "vector": embeddings[idx],
            "concept_id": str(row['concept_id']),
            "concept_name": str(row['concept_name']),
            "domain_id": str(row['domain_id']),
            "vocabulary_id": str(row['vocabulary_id']),
            "concept_class_id": str(row['concept_class_id']),
            "standard_concept": str(row['standard_concept']),
            "concept_code": str(row['concept_code']),
            "valid_start_date": str(row['valid_start_date']),
            "valid_end_date": str(row['valid_end_date']),
            # "invalid_reason": str(row['invalid_reason']),
            # "full_name": str(row['Full Name']),
            "synonyms": str(row['Synonyms']),
            # "definitions": str(row['Definitions']),
            "input_file": file_path
        } for idx, (_, row) in enumerate(batch_df.iterrows())
    ]

    # 插入数据
    try:
        res = client.insert(
            collection_name=collection_name,
            data=data
        )
        logging.info(f"Inserted batch {start_idx // batch_size + 1}, result: {res}")
    except Exception as e:
        logging.error(f"Error inserting batch {start_idx // batch_size + 1}: {e}")

logging.info("Insert process completed.")

# 示例查询
query = "somatic hallucination"
query_embeddings = embedding_function([query])


# 搜索
search_result = client.search(
    collection_name=collection_name,
    data=[query_embeddings[0].tolist()],
    limit=5,
    output_fields=["concept_name", 
                   "synonyms", 
                   "concept_class_id", 
                   ]
)
logging.info(f"Search result for 'Somatic hallucination': {search_result}")

# 查询所有匹配的实体
query_result = client.query(
    collection_name=collection_name,
    filter="concept_name == 'Dyspnea'",
    output_fields=["concept_name", 
                   "synonyms", 
                   "concept_class_id", 
                   ],
    limit=5
)
logging.info(f"Query result for concept_name == 'Dyspnea': {query_result}")



# # 搜索
# # 获取集合的索引信息
# index_info = client.describe_index(collection_name=collection_name,
#                                    index_name="vector")
# logging.info(f"Index info: {index_info}")

# # 根据索引信息设置搜索参数
# if index_info:
#     metric_type = index_info[0].get('metric_type', 'L2')  # 默认使用 L2
#     index_type = index_info[0].get('index_type', 'AUTOINDEX')  # 默认使用 IVF_FLAT
# else:
#     metric_type = 'L2'
#     index_type = 'AUTOINDEX'

# search_params = {
#     "metric_type": metric_type,
#     "params": {"nprobe": 10}
# }

# # 执行搜索
# try:
#     search_result = client.search(
#         collection_name=collection_name,
#         data=[query_embeddings[0].tolist()],
#         anns_field="vector",
#         param=search_params,
#         limit=5,
#         output_fields=["concept_name", "synonyms", "concept_class_id", "full_name"]
#     )
#     logging.info(f"Search result for '{query}': {search_result}")
# except Exception as e:
#     logging.error(f"Error during search: {e}")

# # 查询所有匹配的实体
# query_result = client.query(
#     collection_name=collection_name,
#     filter="concept_class_id == 'Condition'",
#     output_fields=["concept_name", "synonyms", "concept_class_id", "full_name"],
#     limit=5
# )
# logging.info(f"Query result for concept_class_id == 'Condition': {query_result}")

# # 删除查询（注释掉）
# # delete_result = client.delete(
# #     collection_name=collection_name,
# #     filter="concept_class_id == 'Condition'",
# # )
# # logging.info(f"Delete result: {delete_result}")