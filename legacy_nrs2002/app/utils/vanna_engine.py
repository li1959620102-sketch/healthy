import logging
import os
import time
from pathlib import Path
from vanna.ollama.ollama import Ollama
from vanna.chromadb.chromadb_vector import ChromaDB_VectorStore
import pymysql
from app.config import settings

logger = logging.getLogger(__name__)


class HealthRiskVanna(ChromaDB_VectorStore, Ollama):
    def __init__(self, config=None):
        if config is None:
            config = {}

        # --------------------------
        # 关键修改1：强制使用内存模式（不指定本地目录）
        # --------------------------
        config['chroma_db_path'] = None  # 设为None → ChromaDB自动使用内存模式
        config['persist_directory'] = None  # 额外禁用持久化，确保不生成本地文件
        logger.info("✅ 强制使用 ChromaDB 内存模式（不创建本地目录）")

        # 移除本地目录相关代码（无需再处理路径、权限、创建目录）
        # --------------------------

        # 初始化重试逻辑（调整为内存模式专属日志）
        max_retries = 1
        for attempt in range(max_retries):
            try:
                ChromaDB_VectorStore.__init__(self, config=config)
                Ollama.__init__(self, config=config)
                logger.info("✅ ChromaDB 内存模式初始化成功")
                break

            except Exception as e:
                logger.warning(f"ChromaDB 内存模式初始化尝试 {attempt + 1} 失败: {str(e)}")
                if attempt == max_retries - 1:
                    logger.error("❌ ChromaDB 内存模式初始化完全失败，服务无法启动")
                    raise  # 内存模式失败后直接抛出，无本地目录兜底（因本地目录已废弃）
                time.sleep(2)

        # 数据库配置（保持不变）
        self.db_config = {
            'host': settings.MYSQL_HOST,
            'port': int(settings.MYSQL_PORT),
            'user': settings.MYSQL_USER,
            'password': settings.MYSQL_PASSWORD,
            'database': settings.MYSQL_DB,
            'charset': 'utf8mb4'
        }
        self.connection = None

        # --------------------------
        # 关键修改2：删除本地目录验证方法（无需验证不存在的目录）
        # --------------------------

    def connect_to_mysql(self):
        """连接MySQL数据库（保持不变）"""
        try:
            if self.connection is None or not self.connection.open:
                self.connection = pymysql.connect(**self.db_config)
                logger.info("✅ 成功连接到MySQL数据库")
            return True
        except Exception as e:
            logger.error(f"❌ 数据库连接失败: {str(e)}")
            return False

    def execute_sql(self, sql: str):
        """执行SQL查询（保持不变）"""
        try:
            if self.connection is None or not self.connection.open:
                self.connect_to_mysql()

            with self.connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(sql)
                result = cursor.fetchall()
                return result
        except Exception as e:
            logger.error(f"❌ SQL执行失败: {str(e)}")
            try:
                self.connect_to_mysql()
                with self.connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(sql)
                    result = cursor.fetchall()
                    return result
            except Exception as retry_error:
                logger.error(f"❌ SQL重试执行失败: {str(retry_error)}")
                raise retry_error

    def train_health_risk_tables(self):
        """训练健康风险表结构（保持不变，内存模式不影响训练逻辑）"""
        try:
            # 用户健康风险测评记录表的DDL
            ddl = """
            CREATE TABLE IF NOT EXISTS user_health_risk_assessment (
                id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '记录ID',
                user_id VARCHAR(64) NOT NULL COMMENT '用户唯一标识',
                user_name VARCHAR(100) NOT NULL COMMENT '用户姓名',
                sex ENUM('男', '女', '其他') NOT NULL COMMENT '性别',
                age TINYINT UNSIGNED NOT NULL COMMENT '年龄',
                assessment_time DATETIME NOT NULL COMMENT '测评时间',
                assessment_count INT DEFAULT 1 COMMENT '测试次数',
                total_score TINYINT UNSIGNED NOT NULL COMMENT '总分',
                nutritional_impairment_score TINYINT UNSIGNED NOT NULL COMMENT '营养受损分',
                disease_severity_score TINYINT UNSIGNED NOT NULL COMMENT '疾病严重度分',
                age_score TINYINT UNSIGNED NOT NULL COMMENT '年龄分',
                assessment_basis TEXT COMMENT '评分依据说明',
                risk_level ENUM('无风险', '低风险', '中风险', '高风险') NOT NULL COMMENT '风险等级',
                bmi DECIMAL(4,2) COMMENT 'BMI指数',
                weight_change VARCHAR(100) COMMENT '体重变化情况',
                disease_condition TEXT COMMENT '疾病状况描述',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间'
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户健康风险测评记录表';
            """
            self.train(ddl=ddl)

            # 训练业务文档
            documentation = """
            用户健康风险测评记录表包含NRS2002营养风险筛查的完整评分数据。
            重要字段说明：
            - total_score: 总分(0-7分)，分数越高风险越大
            - nutritional_impairment_score: 营养受损评分(0-3分)
            - disease_severity_score: 疾病严重度评分(0-3分)  
            - age_score: 年龄评分(0-1分)，70岁以上为1分
            - risk_level: 风险等级，根据总分自动计算
            - assessment_count: 测评次数，反映用户测评频率

            常用查询模式：
            1. 查询高风险患者：WHERE risk_level = '高风险'
            2. 按时间范围查询：WHERE assessment_time BETWEEN '开始时间' AND '结束时间'
            3. 统计各风险等级人数：GROUP BY risk_level
            4. 查询用户历史测评记录：WHERE user_id is not null
            """
            self.train(documentation=documentation)

            # 训练示例SQL查询
            example_sqls = [
                "SELECT user_name, age, total_score, risk_level FROM user_health_risk_assessment WHERE risk_level = '高风险' ORDER BY total_score DESC LIMIT 10",
                "SELECT risk_level, COUNT(*) as count FROM user_health_risk_assessment GROUP BY risk_level ORDER BY count DESC",
                "SELECT user_name, assessment_time, total_score FROM user_health_risk_assessment WHERE user_id = 'USER001' ORDER BY assessment_time DESC",
                "SELECT AVG(total_score) as avg_score, AVG(age) as avg_age FROM user_health_risk_assessment WHERE sex = '男'",
                "SELECT DATE(assessment_time) as date, COUNT(*) as daily_count FROM user_health_risk_assessment GROUP BY DATE(assessment_time) ORDER BY date DESC LIMIT 7"
            ]
            for sql in example_sqls:
                self.train(sql=sql)

            logger.info("✅ 健康风险表结构训练完成（内存模式）")
        except Exception as e:
            logger.error(f"❌ 训练过程出现错误: {str(e)}")


# --------------------------
# 关键修改3：初始化Vanna实例（不指定任何本地目录配置）
# --------------------------
# 移除本地目录创建代码（os.makedirs(chroma_db_path...)）
vanna_config = {
    'model': settings.LLM_MODEL,
    'ollama_host': settings.OLLAMA_BASE_URL,
    # 不包含 chroma_db_path/persist_directory → 确保使用内存模式
}

logger.info("📌 初始化 Vanna 实例（内存模式，无本地目录）")
vn = HealthRiskVanna(config=vanna_config)

# 可选：启动时自动训练（内存模式下训练数据仅保存在内存，重启后需重新训练）
# vn.train_health_risk_tables()
