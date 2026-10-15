# -*- coding: utf-8 -*-
"""按「中型项目」规模导入真实业务数据。

设计要点
--------
1. **基础语料是真实的**：药食同源物质目录（国家卫健委 4 批 106 种，见 data/seed/food_materials.json）、
   菜库（app/services/recipe_library.py）、知识库文档与片段（Milvus `ai_health_kb_v1` 导出到
   data/seed/kb_fragments.json）、中医体质九分法与食养原则（中华中医药学会 / 卫健委食养指南）。
2. **业务数据按真实分布生成**：注册量按月递增、BMI 与慢病分布符合人群特征、打卡率随依从性衰减、
   咨询满意度右偏；报告分数**调用项目自己的 ScoringEngine 计算**，不另行造分。
3. **可追溯、可回滚**：统一写入 dataset_provenance；各表主键区间登记到 system_config 的
   `seed.active_run`，`--purge` 可按区间精确删除。
4. **不伪造能力**：舌象辨识、OCR 引擎、数字人仍保持"留桩"语义（无识别结果写入）。

用法
----
    python scripts/seed_business_data.py                 # 预演，只打印规模
    python scripts/seed_business_data.py --apply         # 写库
    python scripts/seed_business_data.py --purge         # 删除上一次导入
"""
from __future__ import annotations

import argparse
import json
import math
import random
import string
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pymysql  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.recipe_library import RECIPE_LIBRARY  # noqa: E402
from app.services.scoring_engine import ScoringEngine  # noqa: E402
from app.utils.security import encrypt_phone, hash_password, phone_hash  # noqa: E402

SEED_DIR = ROOT / "data" / "seed"
GEN_VERSION = "1.0.0"
DEMO_PASSWORD = "Demo@2026"          # 种子用户统一演示口令（上线前须清理，见文末报告）
PERIOD_START = date(2024, 10, 1)
PERIOD_END = date(2026, 10, 14)
RUN_MARKER_KEY = "seed.active_run"

# 评分配置（唯一事实源：既写入 system_config，也供生成期缓存使用，二者必须一致）
SCORING_WEIGHTS = {
    "NUTR_SEED_V1": {"basic": 0.4, "diet": 0.35, "chronic": 0.25},
    "CONSTITUTION_V1": {("c%02d" % i): (0.12 if i == 0 else 0.11) for i in range(9)},
    "CHRONIC_RISK_V1": {"pressure": 0.3, "glucose": 0.3, "lipid": 0.2, "weight": 0.2},
    "SUGAR_CARE_V1": {"basic": 0.4, "diet": 0.35, "chronic": 0.25},
    "ELDER_CARE_V1": {"basic": 0.4, "diet": 0.35, "chronic": 0.25},
    "TONGUE_ASSIST_V1": {"tongue": 1.0},
}
SCORING_THRESHOLDS = [
    {"min": 80, "level": "excellent", "name": "优秀"},
    {"min": 60, "level": "good", "name": "良好"},
    {"min": 0, "level": "needs_improvement", "name": "待改善"},
]
SCORING_VERSION = "v1.0"

# 回滚顺序：子表在前、父表在后（无外键约束，靠顺序保证不留孤儿）
PURGE_ORDER = [
    "diet_plan_recipe", "report_annotation", "assessment_report", "assessment_record",
    "diet_plan", "tongue_image", "chat_message", "chat_session",
    "consultation_message", "consultation", "dietitian_customer", "checkin_record",
    "notification", "banquet_guest", "banquet_menu", "travel_plan",
    "ocr_indicator", "ocr_report", "text2sql_query", "user_profile",
    "dietitian_profile", "operation_log", "data_deletion_task", "assessment_template",
    "sys_user",
]

# --------------------------------------------------------------------------- #
# 真实语料
# --------------------------------------------------------------------------- #
SURNAMES = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
GIVEN_CHARS = list("伟芳娜秀英敏静丽强磊军洋勇艳杰娟涛明超秀霞平刚桂英建华文博思远志强欣然雨欣雅静子涵晨曦浩然梦琪嘉怡俊杰志远晓明月欣建国海燕振宇文静伟东春燕立新永强小燕晓东国华建军玉兰淑珍")

CITIES = [
    ("北京", "京菜", "烤鸭、涮羊肉偏滋腻，建议搭配山楂陈皮茶解腻"),
    ("上海", "本帮菜", "浓油赤酱含糖盐较高，糖尿病人群点单时要求少糖少油"),
    ("广州", "粤菜", "清淡重汤水，适合慢病人群；避开老火靓汤的浓嘌呤部分"),
    ("成都", "川菜", "麻辣刺激，胃肠疾病人群选微辣或清汤锅"),
    ("杭州", "杭帮菜", "口味偏甜，控糖人群注意糖醋类菜品"),
    ("西安", "陕菜", "面食为主，建议搭配蔬菜并控制主食总量"),
    ("长沙", "湘菜", "重油重辣，可选清蒸与炖煮类菜品"),
    ("青岛", "鲁菜", "海鲜丰富，高尿酸人群避免与啤酒同食"),
    ("昆明", "滇菜", "菌类食材多，务必选择正规餐馆并彻底加热"),
    ("厦门", "闽菜", "海产丰富，痛风急性期避开贝壳类"),
    ("重庆", "渝菜", "火锅可用清汤锅，蘸料减油减盐"),
    ("南京", "淮扬菜", "刀工精细口味平和，适合大多数人群"),
    ("武汉", "鄂菜", "早餐热干面油脂偏高，建议搭配豆浆与蔬菜"),
    ("哈尔滨", "东北菜", "菜量偏大，建议多人分食并增加凉拌蔬菜"),
    ("乌鲁木齐", "新疆菜", "牛羊肉与烤制食物多，血脂偏高者适量"),
    ("兰州", "西北菜", "面食为主，可加卤蛋与青菜提高蛋白与纤维"),
    ("天津", "津菜", "河海两鲜，注意烹调用油量"),
    ("苏州", "苏菜", "口味偏甜，控糖人群注意酱汁类"),
    ("深圳", "粤菜", "外食选择多，优先蒸煮类"),
    ("三亚", "海南菜", "热带水果丰富，糖尿病人群控制果量"),
    ("贵阳", "黔菜", "酸辣开胃，胃酸过多者适量"),
    ("太原", "晋菜", "醋味突出，面食为主需控制总量"),
    ("福州", "闽菜", "汤菜多，注意盐分"),
    ("合肥", "徽菜", "重油重色，建议搭配清炒蔬菜"),
    ("南昌", "赣菜", "口味偏辣，胃肠敏感者减辣"),
    ("南宁", "桂菜", "酸嘢开胃，注意腌制品盐分"),
    ("呼和浩特", "蒙餐", "奶肉为主，可补充蔬菜与全谷物"),
    ("拉萨", "藏餐", "高原注意饮水与少食多餐"),
    ("银川", "宁夏菜", "清真牛羊肉多，搭配杂粮与蔬菜"),
    ("西宁", "青海菜", "面食与奶制品多，注意总能量"),
]

OCR_INDICATORS = [
    ("空腹血糖", "mmol/L", "3.9-6.1", (4.4, 7.8)),
    ("餐后2小时血糖", "mmol/L", "<7.8", (5.5, 11.5)),
    ("糖化血红蛋白", "%", "4.0-6.0", (4.8, 8.2)),
    ("总胆固醇", "mmol/L", "<5.2", (3.6, 6.8)),
    ("甘油三酯", "mmol/L", "<1.7", (0.6, 3.4)),
    ("低密度脂蛋白胆固醇", "mmol/L", "<3.4", (1.8, 4.6)),
    ("高密度脂蛋白胆固醇", "mmol/L", ">1.0", (0.8, 1.9)),
    ("尿酸", "μmol/L", "男 208-428 / 女 155-357", (240.0, 560.0)),
    ("肌酐", "μmol/L", "男 57-97 / 女 41-73", (48.0, 118.0)),
    ("血尿素氮", "mmol/L", "2.9-8.2", (2.6, 9.4)),
    ("收缩压", "mmHg", "<120", (108.0, 162.0)),
    ("舒张压", "mmHg", "<80", (66.0, 102.0)),
    ("血红蛋白", "g/L", "男 130-175 / 女 115-150", (104.0, 168.0)),
    ("谷丙转氨酶", "U/L", "男 9-50 / 女 7-40", (10.0, 86.0)),
    ("血清白蛋白", "g/L", "40-55", (36.0, 51.0)),
    ("总胆红素", "μmol/L", "3.4-20.5", (7.0, 24.0)),
    ("血清钾", "mmol/L", "3.5-5.3", (3.2, 5.6)),
    ("血清钠", "mmol/L", "137-147", (134.0, 148.0)),
]

CHRONIC_RULES = {
    "高血压": {"low": 120, "high": 165,
               "principles": ["每日食盐不超过 5 克，注意酱油、咸菜、加工肉制品中的隐形盐",
                              "增加新鲜蔬菜与富钾食物（菠菜、香蕉、土豆）有助于钠钾平衡",
                              "控制体重并限制酒精，DASH 饮食模式证据较充分"],
               "foods": "芹菜、菊花、山楂可作日常搭配", "guide": "成人高血压食养指南2023"},
    "糖尿病": {"low": 5.0, "high": 10.6,
               "principles": ["控制总能量与规律进餐，用全谷物、杂豆替代部分精米白面",
                              "蔬菜每日 500 克左右，先吃蔬菜再吃主食有助于平稳餐后血糖",
                              "限制含糖饮料与甜食，血糖平稳时优先选低升糖水果"],
               "foods": "燕麦、薏苡仁、苦瓜、桑叶茶常被提及，仍须在总能量控制前提下使用", "guide": "成人糖尿病食养指南2023"},
    "高血脂": {"low": 4.2, "high": 6.4,
               "principles": ["减少饱和脂肪（肥肉、动物油、奶油）与反式脂肪的摄入",
                              "每周至少 2 次鱼类，增加燕麦、豆类等膳食纤维",
                              "血脂异常常与体重、运动量相关，建议同步改善生活方式"],
               "foods": "山楂、决明子、荷叶茶常用于日常搭配", "guide": "成人高脂血症食养指南2023"},
    "高尿酸血症": {"low": 330.0, "high": 520.0,
                 "principles": ["限制动物内脏、浓肉汤与部分海鲜等高嘌呤食物，急性期更严格",
                                "避免含果糖饮料与酒精（尤其啤酒），每日饮水 2000 毫升以上",
                                "奶类、鸡蛋、新鲜蔬菜嘌呤低，可正常选用"],
                 "foods": "冬瓜、薏米等利湿食材常作日常搭配", "guide": "成人高尿酸血症与痛风食养指南2024"},
}

CONSTITUTIONS = {
    "平和质": "体形匀称健壮，面色红润，精力充沛，睡眠与食欲良好，对外界环境适应能力较强",
    "气虚质": "容易疲乏、气短、自汗，抵抗力偏弱，易感冒，活动后恢复较慢",
    "阳虚质": "平素怕冷，手足不温，喜热饮食，精神不振，大便偏溏",
    "阴虚质": "手足心热，口燥咽干，喜冷饮，面色潮红，容易失眠，大便偏干",
    "痰湿质": "体形肥胖，腹部肥满松软，面部油脂较多，多汗且黏，常感身体沉重",
    "湿热质": "面垢油光，易生痤疮，口苦口干，身重困倦，大便黏滞或燥结",
    "血瘀质": "肤色晦暗，容易出现瘀斑，口唇黯淡，舌下脉络紫黯",
    "气郁质": "情绪易波动，多愁善感，胸胁胀满，容易紧张焦虑",
    "特禀质": "容易过敏，常见打喷嚏、鼻塞、皮肤起风团等表现",
}

# 中医体质辨识（精简版：每型 3 题；正式版每型 7-8 题，待营养师顾问按 ZYYXH/T157-2009 校准）
CONSTITUTION_QUESTIONS = [
    ("平和质", ["精力充沛，不易疲劳", "睡眠质量良好，醒后精神", "对外界环境变化适应较快"]),
    ("气虚质", ["容易疲乏，休息后仍不解乏", "容易气短、说话没力气", "比一般人容易感冒"]),
    ("阳虚质", ["手脚发凉", "比别人怕冷，尤其背部与腹部", "吃凉东西会感到不舒服"]),
    ("阴虚质", ["手心脚心发热", "口燥咽干，总想喝水", "面部两颧容易潮红"]),
    ("痰湿质", ["腹部肥满松软", "感到身体沉重不轻松", "额头油脂分泌较多"]),
    ("湿热质", ["面部油腻，容易生痤疮", "口苦口臭", "大便黏滞，排便不爽"]),
    ("血瘀质", ["皮肤常出现青紫瘀斑", "面色晦暗，容易有黑眼圈", "口唇颜色偏暗"]),
    ("气郁质", ["感到闷闷不乐，情绪低沉", "容易精神紧张、焦虑不安", "多愁善感，容易受惊吓"]),
    ("特禀质", ["容易过敏（药物、食物、气味等）", "皮肤容易出现风团或抓痕", "不感冒也常打喷嚏、鼻塞"]),
]

CHRONIC_RISK_QUESTIONS = [
    ("pressure", "血压相关", ["父母或直系亲属是否有高血压", "近半年是否测出过血压偏高", "是否长期口味偏咸"]),
    ("glucose", "糖代谢相关", ["直系亲属是否有糖尿病", "是否经常感到口干、多饮、多尿", "是否偏爱含糖饮料与甜食"]),
    ("lipid", "血脂相关", ["体检是否提示血脂异常", "是否常吃肥肉、动物油或油炸食品", "每周鱼类摄入是否少于 1 次"]),
    ("weight", "体重与生活方式", ["是否每周中等强度运动少于 3 次", "是否经常熬夜或作息不规律", "是否长期久坐超过 6 小时/天"]),
]

CHAT_QUESTION_BANK = [
    "高血压人群日常饮食要注意什么？", "糖尿病可以吃山药吗？", "枸杞子怎么吃比较好？",
    "血脂偏高应该少吃哪些食物？", "薏米孕妇能吃吗？", "痛风可以喝豆浆吗？",
    "痰湿体质适合吃什么主食？", "经常熬夜的人适合喝什么茶？", "山楂吃多了会不会伤胃？",
    "银耳莲子羹适合什么人喝？", "血压高能不能吃咸菜？", "胆固醇高能吃鸡蛋吗？",
    "茯苓和山药一起煮粥有什么作用？", "气虚体质平时怎么调理饮食？", "秋天干燥适合吃哪些食材？",
    "血糖高可以吃红枣吗？", "高尿酸能吃豆腐吗？", "黑芝麻怎么吃对头发好？",
    "陈皮泡水有什么讲究？", "脾胃虚弱的人早餐吃什么好？", "菊花和枸杞一起泡可以吗？",
    "高血压患者每天盐应该吃多少？", "痛风急性期饮食怎么安排？", "吃粗粮会不会胀气？",
    "血脂高的人能喝牛奶吗？", "孕期适合吃哪些食材？", "糖尿病人主食怎么搭配？",
    "决明子茶能长期喝吗？", "冬天手脚冰凉吃什么好？", "黄芪泡水适合什么人喝？",
    "慢性肾病患者饮食注意什么？", "肥胖人群体重管理怎么吃？", "全谷物每天应该吃多少？",
    "小孩子能吃阿胶吗？", "喝汤能不能代替吃肉？", "桑椹适合什么人吃？",
    "莲子心要去掉吗？", "天天喝绿豆汤好不好？", "生姜早上吃好还是晚上吃好？",
    "体检发现脂肪肝饮食怎么办？", "血压血脂都高怎么安排三餐？", "运动前后应该怎么吃？",
]

DISEASE_POOL = ["高血压", "糖尿病", "高血脂", "高尿酸血症", "慢性肾脏病"]

REFUSAL_BANK = [
    ("孕期能吃山楂吗？对胎儿有影响吗？", "孕期可不可以吃山楂", "孕产期属特殊人群，涉及胎儿影响的内容超出本平台可答复范围，建议咨询产科医师或临床营养师"),
    ("我血糖 13，能不能停掉降糖药？", "用药调整", "调整或停用降糖药物属于医疗行为，本平台不能提供用药建议，请及时就医并遵医嘱"),
    ("高血压吃什么能代替降压药？", "以食代药", "食物不能替代降压药物，本平台只提供一般性饮食参考，具体治疗方案请咨询医师"),
    ("孩子发烧能吃中药膳吗？", "儿童急症", "儿童急性疾病期间的饮食与用药需由医师判断，本平台不提供此类建议"),
]

DIETITIAN_SPECIALTIES = [
    ["糖尿病饮食", "体重管理"], ["高血压饮食", "低盐膳食"], ["孕期营养", "产后调理"],
    ["儿童营养", "生长发育"], ["慢性肾病营养", "低蛋白饮食"], ["高尿酸血症", "痛风饮食"],
    ["血脂管理", "心血管营养"], ["中医食养", "体质调理"], ["老年营养", "肌少症"],
    ["肠道健康", "益生菌应用"], ["肿瘤营养支持", "肿瘤康复"], ["运动营养", "减脂增肌"],
]
CERT_NAMES = ["注册营养师", "公共营养师", "临床营养师", "注册营养技师", "中医食疗调理师"]

DIETITIAN_REPLIES = [
    "根据您的情况，建议先把主食的一半换成全谷物或杂豆，蔬菜每天保证 500 克左右，先吃菜再吃主食。",
    "您提到的这类食材属于药食同源目录内品种，日常少量食用是安全的，但要注意不要用它替代药物治疗。",
    "建议连续记录 3 天的饮食，重点看烹调油和盐的用量，通常控制在每天 25 克油、5 克盐以内效果比较明显。",
    "您的 BMI 和腰围提示需要做体重管理，先定一个每周减少 0.5 公斤的小目标，比快速节食更容易坚持。",
    "这类症状建议以清蒸、炖煮为主，少油炸烧烤；如果指标持续异常，还是要及时复诊。",
    "从报告看您几个维度里膳食结构分数偏低，可以从增加蔬菜和全谷物这两步开始，其他先保持不变。",
]


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def rnd_dt(rng: random.Random, start: date, end: date, hour_bias=None) -> datetime:
    """在区间内随机取时刻；hour_bias 给定时按该小时分布取整点附近。"""
    total_days = (end - start).days
    day = start + timedelta(days=rng.randint(0, total_days)) if total_days > 0 else start
    if hour_bias:
        hour = rng.choices(range(24), weights=hour_bias)[0]
    else:
        hour = rng.randint(6, 23)
    return datetime.combine(day, time(hour, rng.randint(0, 59), rng.randint(0, 59)))


def batch_rows(rows, size):
    buf = []
    for r in rows:
        buf.append(r)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


class Writer:
    """批量写库 + 记录各表主键区间（供精确回滚）。"""

    def __init__(self, conn, batch_size=500):
        self.conn = conn
        self.batch = batch_size
        self.ranges = {}
        self.counts = defaultdict(int)

    def bulk(self, table: str, sql: str, rows, record_ids=True):
        """批量写入。record_ids=True 时登记本表主键区间（回滚依赖它，除 UPDATE 外用为默认）。"""
        cur = self.conn.cursor()
        for chunk in batch_rows(list(rows), self.batch):
            cur.executemany(sql, chunk)
            self.conn.commit()
            self.counts[table] += len(chunk)
            if record_ids:
                # MySQL 语义：executemany 的 lastrowid 是本批「第一条」的自增值；
                # 单线程导入下同一语句内自增连续，故区间为 [first, first+n-1]
                first = cur.lastrowid
                lo, hi = first, first + len(chunk) - 1
                old = self.ranges.get(table)
                self.ranges[table] = [min(lo, old[0]), max(hi, old[1])] if old else [lo, hi]
        cur.close()

    def one(self, table: str, sql: str, params) -> int:
        cur = self.conn.cursor()
        cur.execute(sql, params)
        new_id = cur.lastrowid
        self.conn.commit()
        self.counts[table] += 1
        old = self.ranges.get(table)
        self.ranges[table] = [min(new_id, old[0]), max(new_id, old[1])] if old else [new_id, new_id]
        cur.close()
        return new_id


# --------------------------------------------------------------------------- #
# 模板
# --------------------------------------------------------------------------- #
def build_templates(admin_id: str):
    """返回 (published, fixtures)：published 用于生成测评数据；fixtures 供管理端审核演示。"""
    nutr_questions = [
        {"question_id": "q1", "dimension": "basic", "type": "single", "title": "最近一个月体重变化",
         "options": [{"value": "无明显变化", "score": 3}, {"value": "下降 1-3 kg", "score": 2},
                     {"value": "下降 3 kg 以上", "score": 0}, {"value": "非自主性下降", "score": 0}]},
        {"question_id": "q2", "dimension": "basic", "type": "single", "title": "近两周进食量是否减少",
         "options": [{"value": "没有减少", "score": 3}, {"value": "略有减少", "score": 2}, {"value": "明显减少", "score": 0}]},
        {"question_id": "q3", "dimension": "basic", "type": "single", "title": "BMI（体重 kg ÷ 身高 m²）",
         "options": [{"value": "≥ 18.5", "score": 2}, {"value": "16 ~ 18.5", "score": 1}, {"value": "< 16", "score": 0}]},
        {"question_id": "q4", "dimension": "diet", "type": "single", "title": "每日蔬菜水果摄入量",
         "options": [{"value": "≥ 500 g", "score": 3}, {"value": "200 ~ 500 g", "score": 2}, {"value": "< 200 g", "score": 0}]},
        {"question_id": "q5", "dimension": "diet", "type": "single", "title": "主食是否以全谷物 / 杂豆为主",
         "options": [{"value": "经常", "score": 3}, {"value": "偶尔", "score": 2}, {"value": "几乎不", "score": 0}]},
        {"question_id": "q6", "dimension": "diet", "type": "single", "title": "每日饮水与蛋白质摄入是否充足",
         "options": [{"value": "两者都充足", "score": 3}, {"value": "其一不足", "score": 2}, {"value": "均不足", "score": 0}]},
        {"question_id": "q7", "dimension": "chronic", "type": "single", "title": "是否确诊高血压 / 糖尿病 / 高血脂",
         "options": [{"value": "都没有", "score": 3}, {"value": "1 种", "score": 2}, {"value": "2 种及以上", "score": 0}]},
        {"question_id": "q8", "dimension": "chronic", "type": "single", "title": "每周中等强度运动次数",
         "options": [{"value": "≥ 3 次", "score": 3}, {"value": "1 ~ 2 次", "score": 2}, {"value": "基本不运动", "score": 0}]},
    ]
    nutr_dims = [{"key": "basic", "name": "基础营养状况", "weight": 0.4},
                 {"key": "diet", "name": "膳食结构", "weight": 0.35},
                 {"key": "chronic", "name": "慢病与生活方式", "weight": 0.25}]

    cons_dims = []
    cons_questions = []
    for i, (name, items) in enumerate(CONSTITUTION_QUESTIONS):
        key = "c%02d" % i
        cons_dims.append({"key": key, "name": name + "倾向", "weight": 0.12 if i == 0 else 0.11})
        for j, title in enumerate(items):
            cons_questions.append({
                "question_id": f"{key}q{j + 1}", "dimension": key, "type": "single",
                "title": title + f"（{name}相关表现，近一年内）",
                "options": [{"value": "没有", "score": 3}, {"value": "很少", "score": 2},
                            {"value": "有时", "score": 1}, {"value": "经常", "score": 0}],
            })

    risk_dims = [{"key": "pressure", "name": "血压相关风险", "weight": 0.3},
                 {"key": "glucose", "name": "糖代谢相关风险", "weight": 0.3},
                 {"key": "lipid", "name": "血脂相关风险", "weight": 0.2},
                 {"key": "weight", "name": "体重与生活方式", "weight": 0.2}]
    risk_questions = []
    for key, dim_name, items in CHRONIC_RISK_QUESTIONS:
        for j, title in enumerate(items):
            risk_questions.append({
                "question_id": f"{key}q{j + 1}", "dimension": key, "type": "single",
                "title": title,
                "options": [{"value": "否", "score": 2}, {"value": "不确定", "score": 1}, {"value": "是", "score": 0}],
            })

    published = [
        {"code": "CONSTITUTION_V1", "version": 1, "name": "中医体质辨识（九种体质）",
         "type": "constitution", "description": "依据中华中医药学会《中医体质分类与判定》思路设计的精简版自评，用于食养方向判断。",
         "dimension_config": cons_dims, "question_config": cons_questions},
        {"code": "CHRONIC_RISK_V1", "version": 1, "name": "慢病风险自评（血压 · 糖代谢 · 血脂 · 体重）",
         "type": "risk", "description": "面向常见慢性非传染性疾病的危险因素自评，用于分层跟进与食养干预建议。",
         "dimension_config": risk_dims, "question_config": risk_questions},
    ]

    fixtures = [
        {"code": "SUGAR_CARE_V1", "version": 1, "name": "糖尿病食养专项测评",
         "type": "nutrition", "status": "pending", "description": "控糖人群膳食结构与餐次分配专项评估（待审核）。",
         "dimension_config": nutr_dims, "question_config": nutr_questions[:6]},
        {"code": "ELDER_CARE_V1", "version": 1, "name": "老年营养风险筛查（简化）",
         "type": "risk", "status": "draft", "description": "面向 65 岁以上人群的营养风险初筛（草稿，待补充题目）。",
         "dimension_config": nutr_dims, "question_config": nutr_questions[:3]},
        {"code": "TONGUE_ASSIST_V1", "version": 1, "name": "舌象辅助辨识（试点）",
         "type": "tongue", "status": "offline", "description": "舌象采集与辅助辨识试点，辨识引擎达标前不对外开放。",
         "dimension_config": [{"key": "tongue", "name": "舌象特征", "weight": 1.0}],
         "question_config": [{"question_id": "t1", "dimension": "tongue", "type": "single", "title": "舌色自我描述",
                              "options": [{"value": "淡红", "score": 3}, {"value": "偏淡", "score": 2}, {"value": "偏红", "score": 1}]}]},
    ]
    return published, fixtures


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def parse_args():
    p = argparse.ArgumentParser(description="按中型规模导入真实业务数据")
    p.add_argument("--users", type=int, default=5000)
    p.add_argument("--dietitians", type=int, default=36)
    p.add_argument("--assessments", type=int, default=15000)
    p.add_argument("--plans", type=int, default=6000)
    p.add_argument("--chat-sessions", type=int, default=6000)
    p.add_argument("--consultations", type=int, default=2400)
    p.add_argument("--checkins", type=int, default=72000)
    p.add_argument("--notifications", type=int, default=21000)
    p.add_argument("--oplogs", type=int, default=26000)
    p.add_argument("--banquets", type=int, default=720)
    p.add_argument("--travels", type=int, default=960)
    p.add_argument("--ocrs", type=int, default=480)
    p.add_argument("--text2sql", type=int, default=620)
    p.add_argument("--seed", type=int, default=20261015)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--purge", action="store_true")
    return p.parse_args()


def connect():
    return pymysql.connect(
        host=settings.MYSQL_HOST, port=int(settings.MYSQL_PORT),
        user=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD,
        database=settings.MYSQL_DB, charset="utf8mb4", autocommit=False,
    )


def purge(conn) -> int:
    """删除本脚本导入的数据。

    口径：以**导入前各表的 max(id)** 为基线，删除基线之后新增的行（子表先删）。
    相比"登记区间"，基线口径在导入中途失败（区间尚未登记完）时仍然可用。
    ⚠️ 若导入之后有其它写入（如真实用户注册），这些行也会被一并删除，故 purge 会逐表打印删除行数。
    """
    cur = conn.cursor()
    cur.execute("SELECT config_value FROM system_config WHERE config_key=%s", (RUN_MARKER_KEY,))
    row = cur.fetchone()
    if not row:
        print("[i] 没有找到导入登记，无需清理")
        return 0
    info = json.loads(row[0])
    baseline = info.get("baseline") or {}
    if not baseline:
        print("[i] 登记中没有基线信息（可能是旧版本登记），请用 --by-range 或人工核对")
        return 0
    removed = 0
    for table in PURGE_ORDER:
        base = baseline.get(table)
        if base is None:
            continue
        cur.execute(f"DELETE FROM {table} WHERE id > %s", (base,))
        if cur.rowcount:
            print(f"    - {table:24s} 删除 {cur.rowcount:,}")
        removed += cur.rowcount
    conn.commit()
    cur.execute("DELETE FROM dataset_provenance WHERE run_id=%s", (info.get("run_id"),))
    cur.execute("DELETE FROM system_config WHERE config_key=%s", (RUN_MARKER_KEY,))
    conn.commit()
    print(f"[OK] 共删除 {removed:,} 行，登记已清除")
    return removed


def main() -> int:
    args = parse_args()
    conn = connect()
    if args.purge:
        return purge(conn)

    rng = random.Random(args.seed)
    run_id = "seed-" + datetime.now().strftime("%Y%m%d%H%M%S")
    materials = json.loads((SEED_DIR / "food_materials.json").read_text(encoding="utf-8"))["items"]
    kb = json.loads((SEED_DIR / "kb_fragments.json").read_text(encoding="utf-8"))["docs"]
    kb_by_key = {d["source"]: d for d in kb}
    cur = conn.cursor()

    cur.execute("SELECT user_id FROM sys_user WHERE role='admin' AND deleted_at IS NULL ORDER BY id LIMIT 1")
    admin_row = cur.fetchone()
    if not admin_row:
        raise SystemExit("找不到管理员账号，请先启动一次服务完成初始化")
    admin_id = admin_row[0]

    # 幂等：先清掉本脚本自有编码的模板（防止上次中断运行的残留），再统计既有已发布模板。
    # 注意：仅在 --apply 时才动库，预演必须保持只读。
    published, fixtures = build_templates(admin_id)
    seed_codes = [t["code"] for t in published + fixtures]
    if args.apply and seed_codes:
        cur.execute("DELETE FROM assessment_template WHERE code IN (%s)"
                    % ",".join(["%s"] * len(seed_codes)), tuple(seed_codes))
        conn.commit()
    cur.execute("SELECT id, code, version FROM assessment_template WHERE status='published'")
    existing = cur.fetchall()
    plan_scale = {
        "users": args.users, "dietitians": args.dietitians, "assessments": args.assessments,
        "plans": args.plans, "chat_sessions": args.chat_sessions, "consultations": args.consultations,
        "checkins": args.checkins, "notifications": args.notifications, "oplogs": args.oplogs,
        "banquets": args.banquets, "travels": args.travels, "ocrs": args.ocrs, "text2sql": args.text2sql,
    }
    print(json.dumps({"run_id": run_id, "scale": plan_scale, "apply": args.apply},
                     ensure_ascii=False, indent=2))
    if not args.apply:
        print("当前为预演模式；确认后追加 --apply 才会写库。")
        return 0

    w = Writer(conn)

    # 导入前记录各表 max(id) 基线并立即登记：即使中途失败，也能用 --purge 精确回滚
    baseline = {}
    for _t in PURGE_ORDER:
        cur.execute(f"SELECT COALESCE(MAX(id),0) FROM {_t}")
        baseline[_t] = cur.fetchone()[0]
    cur.execute("INSERT INTO system_config (config_key,config_value,description,updated_by,updated_at) "
                "VALUES (%s,%s,%s,%s,NOW()) ON DUPLICATE KEY UPDATE config_value=VALUES(config_value),"
                "description=VALUES(description),updated_at=NOW()",
                (RUN_MARKER_KEY, json.dumps({"run_id": run_id, "baseline": baseline}, ensure_ascii=False),
                 "最后一次批量导入的基线快照（供 --purge 精确回滚，勿手工修改）", admin_id))
    conn.commit()

    # ---------------- 1. 模板 ---------------- #
    template_map = {}
    tid2type = {}
    for tpl in published:
        tid = w.one("assessment_template",
                    "INSERT INTO assessment_template (code,version,name,type,description,dimension_config,"
                    "question_config,status,created_by,created_at,submitted_at,audit_at,auditor_id,published_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,'published',%s,%s,%s,%s,%s,%s)",
                    (tpl["code"], tpl["version"], tpl["name"], tpl["type"], tpl["description"],
                     json.dumps(tpl["dimension_config"], ensure_ascii=False),
                     json.dumps(tpl["question_config"], ensure_ascii=False), admin_id,
                     (dt := rnd_dt(rng, PERIOD_START, PERIOD_START + timedelta(days=30))),
                     dt, dt + timedelta(hours=6), admin_id, dt + timedelta(days=1)))
        template_map[tpl["code"]] = (tid, tpl["version"], tpl["dimension_config"], tpl["question_config"])
    for tpl in fixtures:
        created = rnd_dt(rng, PERIOD_START + timedelta(days=200), PERIOD_END)
        w.one("assessment_template",
              "INSERT INTO assessment_template (code,version,name,type,description,dimension_config,"
              "question_config,status,created_by,created_at,submitted_at,audit_at,auditor_id,audit_remark,published_at,offline_at) "
              "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (tpl["code"], tpl["version"], tpl["name"], tpl["type"], tpl["description"],
               json.dumps(tpl["dimension_config"], ensure_ascii=False),
               json.dumps(tpl["question_config"], ensure_ascii=False), tpl["status"], admin_id, created,
               created + timedelta(days=1) if tpl["status"] != "draft" else None,
               created + timedelta(days=2) if tpl["status"] == "offline" else None,
               admin_id, "题量与权重需营养师顾问复核" if tpl["status"] == "pending" else None,
               created + timedelta(days=3) if tpl["status"] == "offline" else None,
               created + timedelta(days=30) if tpl["status"] == "offline" else None))
    # 已发布模板（含既有 NUTR_SEED_V1）按数据库里的真实配置纳入抽样池
    for tid, code, ver in existing:
        cur.execute("SELECT type,dimension_config,question_config FROM assessment_template WHERE id=%s", (tid,))
        t, dims_raw, qs_raw = cur.fetchone()
        template_map[code] = (tid, ver, json.loads(dims_raw), json.loads(qs_raw))
        tid2type[tid] = t
    tpl_pool = [(tid, ver, dims, qs) for code, (tid, ver, dims, qs) in template_map.items()]
    for tpl in published:
        tid2type[template_map[tpl["code"]][0]] = tpl["type"]

    # ---------------- 2. 用户 ---------------- #
    existing_phones = set()
    cur.execute("SELECT phone_hash FROM sys_user WHERE phone_hash IS NOT NULL")
    existing_phones = {r[0] for r in cur.fetchall()}
    pw_hashes = [hash_password(DEMO_PASSWORD) for _ in range(8)]

    used_usernames = set()
    cur.execute("SELECT username FROM sys_user")
    used_usernames = {r[0] for r in cur.fetchall()}

    def gen_phone() -> str:
        while True:
            p = rng.choice(["130", "131", "133", "135", "136", "137", "138", "139", "150", "151", "152",
                            "155", "156", "157", "158", "159", "170", "173", "176", "177", "180", "182",
                            "183", "185", "186", "187", "188", "189", "191", "193", "195", "199"])
            p += "".join(rng.choice("0123456789") for _ in range(8))
            h = phone_hash(p)
            if h not in existing_phones:
                existing_phones.add(h)
                return p

    def gen_user_id() -> str:
        return "U" + "".join(rng.choice("0123456789ABCDEF") for _ in range(12))

    def gen_username() -> str:
        while True:
            u = "u" + "".join(rng.choice("0123456789abcdef") for _ in range(11))
            if u not in used_usernames:
                used_usernames.add(u)
                return u

    def gen_name(sex: str) -> str:
        given = "".join(rng.sample(GIVEN_CHARS, 1)) if rng.random() < 0.35 else "".join(rng.sample(GIVEN_CHARS, 2))
        return rng.choice(SURNAMES) + given

    # 注册量按月递增（产品冷启动 → 稳步增长）
    def growth_day() -> date:
        span = (PERIOD_END - PERIOD_START).days
        x = rng.random() ** 0.55          # 偏向近期
        return PERIOD_START + timedelta(days=int(span * x))

    users = []
    for i in range(args.users):
        sex = "男" if rng.random() < 0.46 else "女"
        age = int(min(85, max(18, rng.gauss(42, 14))))
        created = rnd_dt(rng, PERIOD_START, PERIOD_END)
        created = datetime.combine(growth_day(), created.time())
        last_login = min(datetime.combine(PERIOD_END, time(23, 59)),
                         created + timedelta(days=max(1, int(abs(rng.gauss(6, 9))))))
        status = "disabled" if rng.random() < 0.03 else "active"
        users.append({"uid": gen_user_id(), "un": gen_username(), "name": gen_name(sex), "sex": sex,
                      "age": age, "created": created, "last_login": last_login, "status": status,
                      "last_active": max(last_login, min(datetime.combine(PERIOD_END, time(23, 59)),
                                                          created + timedelta(days=rng.randint(1, 90))))})

    w.bulk("sys_user",
           "INSERT INTO sys_user (user_id,username,password_hash,real_name,sex,age,role,status,"
           "created_at,last_login_at,last_active_at) VALUES (%s,%s,%s,%s,%s,%s,'user',%s,%s,%s,%s)",
           ((u["uid"], u["un"], rng.choice(pw_hashes), u["name"], u["sex"], u["age"],
             u["status"], u["created"], u["last_login"], u["last_active"]) for u in users), record_ids=True)
    print(f"[i] 用户 {len(users):,}")

    # 手机号（85% 用户已绑定，模拟手机号注册为主通道）
    phone_rows = []
    for u in users:
        if rng.random() < 0.85:
            p = gen_phone()
            phone_rows.append((encrypt_phone(p), phone_hash(p), u["uid"]))
    w.bulk("sys_user(phone)", "UPDATE sys_user SET phone_cipher=%s, phone_hash=%s WHERE user_id=%s", phone_rows,
           record_ids=False)
    print(f"[i] 手机号绑定 {len(phone_rows):,}")

    user_ids = [u["uid"] for u in users]
    by_uid = {u["uid"]: u for u in users}

    # 营养师 / 管理员
    dietitian_ids = []
    d_rows = []
    d_profiles = []
    for i in range(args.dietitians):
        uid = gen_user_id()
        sex = "男" if rng.random() < 0.35 else "女"
        created = rnd_dt(rng, PERIOD_START, PERIOD_END - timedelta(days=30))
        d_rows.append({"uid": uid, "un": gen_username(), "name": gen_name(sex), "sex": sex,
                       "age": rng.randint(26, 58), "created": created})
        dietitian_ids.append(uid)
        audit = rng.choices(["approved", "pending", "rejected", "expired"], weights=[78, 10, 6, 6])[0]
        d_profiles.append((uid, rng.choice(CERT_NAMES), "ZS" + "".join(rng.choice(string.digits) for _ in range(10)),
                           "minio://nutritionist/cert/%s.jpg" % uid.lower(),
                           "minio://nutritionist/work/%s.jpg" % uid.lower(),
                           json.dumps(rng.sample(DIETITIAN_SPECIALTIES, rng.randint(1, 2)), ensure_ascii=False),
                           "从业 %d 年，累计服务客户 %d 例，擅长%s。" % (rng.randint(3, 20), rng.randint(80, 900),
                                                                  "、".join(rng.choice(DIETITIAN_SPECIALTIES[0]))),
                           audit, created + timedelta(days=rng.randint(1, 10)), admin_id,
                           "资质材料不清晰，请重新上传" if audit == "rejected" else None,
                           date(2027, 12, 31)))
    w.bulk("sys_user", "INSERT INTO sys_user (user_id,username,password_hash,real_name,sex,age,role,status,"
                       "created_at,last_login_at,last_active_at) VALUES (%s,%s,%s,%s,%s,%s,'dietitian','active',%s,%s,%s)",
           ((r["uid"], r["un"], rng.choice(pw_hashes), r["name"], r["sex"], r["age"], r["created"],
             r["created"] + timedelta(days=1), r["created"] + timedelta(days=2)) for r in d_rows), record_ids=True)
    w.bulk("dietitian_profile",
           "INSERT INTO dietitian_profile (user_id,cert_name,cert_no,cert_url,work_proof_url,specialties,cases,"
           "audit_status,audit_at,auditor_id,reject_reason,cert_expire_at,created_at,updated_at) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
           ((p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9], p[10], p[11], p[8], p[8]) for p in d_profiles),
           record_ids=True)
    print(f"[i] 营养师 {len(dietitian_ids)}")

    # ---------------- 3. 健康档案 ---------------- #
    profile_users = rng.sample(user_ids, int(len(user_ids) * 0.86))
    prof_rows = []
    user_ctx = {}
    for uid in profile_users:
        u = by_uid[uid]
        h = rng.randint(152, 188)
        # BMI：正常 44% / 超重 32% / 肥胖 15% / 偏瘦 9%
        bucket = rng.choices(["normal", "over", "obese", "thin"], weights=[44, 32, 15, 9])[0]
        target = {"normal": rng.uniform(18.6, 23.8), "over": rng.uniform(24.1, 27.9),
                  "obese": rng.uniform(28.1, 33.5), "thin": rng.uniform(16.6, 18.4)}[bucket]
        weight = round(target * (h / 100) ** 2, 1)
        diseases = []
        if rng.random() < 0.30 + (0.30 if bucket in ("over", "obese") else 0) + (u["age"] - 18) / 260:
            diseases.append("高血压")
        if rng.random() < 0.22 + (0.20 if bucket in ("over", "obese") else 0) + (u["age"] - 18) / 300:
            diseases.append("糖尿病")
        if rng.random() < 0.24 + (u["age"] - 18) / 320:
            diseases.append("高血脂")
        if rng.random() < 0.14:
            diseases.append("高尿酸血症")
        if rng.random() < 0.04:
            diseases.append("慢性肾脏病")
        prefs = rng.sample(["清淡", "低盐", "控糖", "低脂", "均衡", "素食", "少油", "忌辛辣"],
                           rng.choices([1, 2], weights=[65, 35])[0])
        allergy = rng.choices([[], ["海鲜鱼类"], ["牛奶及乳制品"], ["花生坚果"], ["蛋类"]],
                              weights=[72, 9, 7, 7, 5])[0]
        taboo = rng.choices([[], ["辛辣"], ["动物内脏"], ["高糖甜品"], ["生冷"], ["海鲜发物"]],
                            weights=[58, 12, 10, 9, 6, 5])[0]
        created = u["created"] + timedelta(days=rng.randint(0, 5))
        prof_rows.append((uid, h, weight, json.dumps(diseases, ensure_ascii=False).encode("utf-8"),
                          "、".join(prefs), json.dumps({"labels": allergy, "keywords": allergy}, ensure_ascii=False),
                          json.dumps({"labels": taboo, "keywords": taboo}, ensure_ascii=False), created, created))
        user_ctx[uid] = {"h": h, "w": weight, "bmi": round(weight / (h / 100) ** 2, 1),
                         "diseases": diseases, "allergy": allergy, "taboo": taboo, "prefs": prefs}
    w.bulk("user_profile",
           "INSERT INTO user_profile (user_id,height_cm,weight_kg,diseases_cipher,diet_preference,"
           "allergies_json,taboo_json,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
           prof_rows, record_ids=True)
    print(f"[i] 健康档案 {len(prof_rows):,}")

    # ---------------- 4. 测评记录 / 报告 ---------------- #
    def health_index(uid: str) -> float:
        ctx = user_ctx.get(uid)
        u = by_uid[uid]
        if not ctx:
            return max(0.25, min(0.95, 1 - (u["age"] - 25) / 90))
        h = 0.82 - 0.09 * len(ctx["diseases"]) - max(0.0, ctx["bmi"] - 24) * 0.012 - max(0.0, u["age"] - 40) * 0.004
        return max(0.15, min(0.98, h))

    def pick_answer(rng, options, h):
        """按健康指数在选项间采样：最优选项分最高。"""
        ordered = sorted(range(len(options)), key=lambda i: -options[i]["score"])
        best = ordered[:max(1, len(ordered) // 2)]
        worst = ordered[max(1, len(ordered) // 2):]
        return options[rng.choice(best if rng.random() < h else worst)]["value"]

    ref_docs = list(kb_by_key.values())
    record_rows = []
    records = []
    for i in range(args.assessments):
        uid = rng.choice(user_ids)
        tid, ver, dims, qs = tpl_pool[i % len(tpl_pool)]
        h = health_index(uid)
        answers = {}
        for q in qs:
            if q.get("type") == "multi":
                continue
            answers[q["question_id"]] = pick_answer(rng, q["options"], h)
        ctx = user_ctx.get(uid, {})
        snapshot = {"bmi": ctx.get("bmi"), "diseases": ctx.get("diseases", []),
                    "diet_preference": ctx.get("prefs", [])} if ctx else {}
        started = rnd_dt(rng, PERIOD_START, PERIOD_END,
                         hour_bias=[1, 0, 0, 0, 0, 1, 3, 6, 9, 11, 10, 8, 7, 6, 7, 8, 9, 11, 12, 11, 9, 7, 5, 3])
        done = rng.random() < 0.93
        status = "completed" if done else ("void" if rng.random() < 0.4 else "in_progress")
        rows = {"uid": uid, "tpl": tid, "ver": ver, "snapshot": json.dumps(snapshot, ensure_ascii=False),
                "answers": json.dumps(answers, ensure_ascii=False), "status": status,
                "progress": f"{len(answers)}/{len(qs)}", "started": started}
        records.append(rows)
        record_rows.append((uid, None, tid, ver, rows["snapshot"], status, rows["answers"],
                            len(answers), rows["progress"], None if done else f"seed-{run_id}-{i}",
                            started, started, started + timedelta(days=30),
                            started + timedelta(minutes=rng.randint(3, 20)) if done else None,
                            started + timedelta(days=2) if status == "void" else None))
    w.bulk("assessment_record",
           "INSERT INTO assessment_record (user_id,guest_token,template_id,template_version,profile_snapshot,"
           "status,answers,current_page,progress,idempotency_key,started_at,last_saved_at,expire_at,submitted_at,voided_at) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", record_rows, record_ids=True)
    print(f"[i] 测评记录 {len(record_rows):,}")

    cur.execute("SELECT id,user_id,template_id,template_version,profile_snapshot,answers,submitted_at "
                "FROM assessment_record WHERE id > %s AND status='completed' ORDER BY id",
                (baseline["assessment_record"],))
    rec_rows = cur.fetchall()
    # ScoringEngine 每次算分都会按需读 system_config；1.4 万次评分若逐次查库会成为瓶颈，
    # 这里用与即将写入的 system_config 完全一致的配置做进程内缓存（脚本内 monkeypatch，仅影响本进程）。
    import app.services.scoring_engine as _se
    _cfg = {
        "scoring.weights": json.dumps(SCORING_WEIGHTS, ensure_ascii=False),
        "scoring.thresholds": json.dumps(SCORING_THRESHOLDS, ensure_ascii=False),
        "scoring.version": SCORING_VERSION,
    }
    _se._read_config = lambda key: _cfg.get(key)
    report_rows = []
    report_ids = []
    for rid, uid, tid, ver, snap, answers_json, submitted in rec_rows:
        dims, qs = template_map_by_id(tid, template_map, tpl_pool)
        tpl_obj = type("T", (), {"code": code_by_id(tid, template_map), "dimension_config": dims,
                                 "question_config": qs})()
        res = ScoringEngine.score(tpl_obj, json.loads(answers_json))
        weak = sorted(res["dimension_scores"], key=lambda x: x["score"])[:2]
        ctx = user_ctx.get(uid, {})
        diseases = ctx.get("diseases", [])
        # 引用：优先与慢病/体质相关的真实文档
        keys = []
        for d in diseases:
            rule = CHRONIC_RULES.get(d)
            if rule and rule["guide"] in kb_by_key:
                keys.append(rule["guide"])
        keys += [d["source"] for d in ref_docs]
        picked = []
        seen = set()
        for k in keys:
            if k in seen or k not in kb_by_key:
                continue
            seen.add(k)
            frag = rng.choice(kb_by_key[k]["fragments"])
            picked.append({"score": round(rng.uniform(0.56, 0.87), 3), "title": kb_by_key[k]["file_name"],
                           "source": kb_by_key[k]["source"], "fragment": frag})
            if len(picked) >= rng.randint(2, 3):
                break
        principles = []
        for d in diseases:
            rule = CHRONIC_RULES.get(d)
            if rule:
                principles.append((d, rule))
        analysis = build_analysis(res, weak, principles, ctx, rng)
        state = "complete" if rng.random() < 0.965 else ("basic" if rng.random() < 0.7 else "failed")
        gen_at = submitted + timedelta(seconds=rng.randint(8, 60))
        report_rows.append((rid, uid, tid, ver, state, res["score"], res["standardized_score"], res["level"],
                            json.dumps(res["dimension_scores"], ensure_ascii=False),
                            json.dumps(res["suggestions"], ensure_ascii=False), analysis,
                            json.dumps(picked, ensure_ascii=False), "本内容由 AI 生成，不构成诊疗建议",
                            "Qwen/Qwen2.5-72B-Instruct", "seed-v1", rng.randint(900, 4200),
                            0 if state == "complete" else rng.randint(1, 2), gen_at, gen_at))
    w.bulk("assessment_report",
           "INSERT INTO assessment_report (record_id,user_id,template_id,template_version,state,score,"
           "standardized_score,level,dimension_scores,suggestions,analysis_text,`references`,disclaimer,"
           "model_name,prompt_version,elapsed_ms,retry_count,generated_at,updated_at) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", report_rows, record_ids=True)
    print(f"[i] 测评报告 {len(report_rows):,}")

    cur.execute("SELECT id,user_id,record_id,template_id FROM assessment_report "
                "WHERE id > %s AND state='complete' ORDER BY id",
                (baseline["assessment_report"],))
    complete_reports = cur.fetchall()

    # ---------------- 5. 食养方案 + 食谱 ---------------- #
    plan_targets = rng.sample(complete_reports, min(args.plans, len(complete_reports)))
    plan_rows = []
    for rep_id, uid, rec_id, rep_tid in plan_targets:
        ctx = user_ctx.get(uid, {})
        diseases = ctx.get("diseases", [])
        if "糖尿病" in diseases:
            scenario = "控糖"
        elif "高血压" in diseases:
            scenario = "高血压"
        elif ctx and ctx.get("bmi", 22) >= 28:
            scenario = "痰湿"
        else:
            scenario = "通用"
        cons = "平和质"
        if scenario == "痰湿":
            cons = "痰湿质"
        elif scenario == "控糖":
            cons = "阴虚质"
        elif scenario == "高血压":
            cons = "气郁质"
        period = "month" if rng.random() < 0.1 else "week"
        days = 30 if period == "month" else 7
        title = f"{cons}调理 · {'月度' if period == 'month' else '每周'}食养方案"
        gen_at = rnd_dt(rng, PERIOD_START, PERIOD_END)
        plan_rows.append((uid, rep_id, tid2type.get(rep_tid, "nutrition"), period, title, cons,
                          json.dumps(diseases, ensure_ascii=False),
                          json.dumps(ctx.get("taboo", []), ensure_ascii=False),
                          build_plan_summary(cons, diseases, period, scenario),
                          "complete", json.dumps(pick_plan_refs(kb_by_key, diseases, rng), ensure_ascii=False),
                          "本方案由 AI 生成，仅作膳食参考，不构成诊疗建议", gen_at, gen_at, scenario, days))
    for row in plan_rows:
        pid = w.one("diet_plan",
                    "INSERT INTO diet_plan (user_id,report_id,template_type,period,title,constitution,"
                    "target_symptoms,avoid_foods,summary_text,state,`references`,disclaimer,generated_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    row[:14])
        lib = RECIPE_LIBRARY[row[14]]
        recipe_rows = []
        for day in range(1, row[15] + 1):
            for mi, meal in enumerate(("breakfast", "lunch", "dinner")):
                pool = lib[meal]
                dish = pool[(day - 1 + mi) % len(pool)]
                recipe_rows.append((pid, meal, day, dish["dish_name"],
                                    json.dumps(dish["ingredients"], ensure_ascii=False),
                                    dish["efficacy"], dish["caution"]))
        w.bulk("diet_plan_recipe",
               "INSERT INTO diet_plan_recipe (plan_id,meal_type,day_no,dish_name,ingredients,efficacy,caution) "
               "VALUES (%s,%s,%s,%s,%s,%s,%s)", recipe_rows)
    print(f"[i] 食养方案 {len(plan_rows):,} / 食谱 {w.counts['diet_plan_recipe']:,}")

    # ---------------- 6. 智能问答 ---------------- #
    sess_rows = []
    sess_meta = []
    for i in range(args.chat_sessions):
        uid = rng.choice(user_ids)
        q = rng.choice(CHAT_QUESTION_BANK) if rng.random() < 0.75 else rng.choice(REFUSAL_BANK)[0]
        created = rnd_dt(rng, PERIOD_START, PERIOD_END,
                         hour_bias=[2, 1, 1, 1, 1, 2, 4, 7, 9, 10, 9, 8, 8, 7, 8, 9, 10, 11, 12, 11, 10, 8, 6, 4])
        rounds = rng.choices([1, 2, 3, 4], weights=[46, 28, 16, 10])[0]
        sess_meta.append({"uid": uid, "title": q[:40], "created": created, "rounds": rounds})
    w.bulk("chat_session", "INSERT INTO chat_session (user_id,guest_token,title,message_count,context_summary,"
                           "created_at,last_msg_at) VALUES (%s,NULL,%s,%s,NULL,%s,%s)",
           ((m["uid"], m["title"], m["rounds"] * 2, m["created"],
             m["created"] + timedelta(minutes=m["rounds"] * rng.randint(1, 4))) for m in sess_meta),
           record_ids=True)

    cur.execute("SELECT id,user_id,title,created_at FROM chat_session WHERE id > %s ORDER BY id",
                (baseline["chat_session"],))
    sessions = cur.fetchall()
    msg_rows = []
    for (sid, uid, title, created), meta in zip(sessions, sess_meta):
        n = meta["rounds"]
        for r in range(n):
            t = created + timedelta(minutes=r * 3)
            msg_rows.append((sid, "user", r * 2, title if r == 0 else followup_question(rng, title), None, 0, None, None, t))
            refused = any(k in title for k, _ in [("停掉", ""), ("代替", ""), ("胎儿", ""), ("孩子", "")]) or rng.random() < 0.03
            if refused:
                ans = next((x[2] for x in REFUSAL_BANK if x[0][:6] in title), REFUSAL_BANK[0][2])
                msg_rows.append((sid, "assistant", r * 2 + 1, ans, None, 1, "特殊人群/医疗干预", None, t + timedelta(seconds=2)))
            else:
                ans, refs = build_chat_answer(title, kb_by_key, rng)
                msg_rows.append((sid, "assistant", r * 2 + 1, ans, json.dumps(refs, ensure_ascii=False), 0, None,
                                 rng.randint(40, 320), t + timedelta(seconds=rng.randint(3, 25))))
    flat = [(m[0], m[1], m[3], m[4], m[5], m[6], m[7], m[8]) for m in msg_rows]
    w.bulk("chat_message", "INSERT INTO chat_message (session_id,role,content,`references`,refused,refuse_reason,"
                           "elapsed_ms,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", flat)
    print(f"[i] 会话 {len(sessions):,} / 消息 {len(flat):,}")

    # ---------------- 7. 人工咨询 ---------------- #
    approved_dietitians = [p[0] for p in d_profiles if p[7] == "approved"] or dietitian_ids
    cons_rows = []
    for i in range(args.consultations):
        uid = rng.choice(user_ids)
        did = rng.choice(approved_dietitians) if rng.random() < 0.75 else None
        created = rnd_dt(rng, PERIOD_START, PERIOD_END)
        status = rng.choices(["closed", "active", "pending", "timeout"], weights=[64, 14, 12, 10])[0]
        accepted = created + timedelta(minutes=rng.randint(2, 240)) if status in ("closed", "active") else None
        closed = accepted + timedelta(minutes=rng.randint(10, 90)) if status == "closed" and accepted else None
        rating = rng.choices([5, 4, 3, 2, 1], weights=[52, 30, 12, 4, 2])[0] if status == "closed" else None
        cons_rows.append({"uid": uid, "did": did, "type": rng.choices(["human", "ai"], weights=[82, 18])[0],
                          "status": status, "created": created, "accepted": accepted, "closed": closed,
                          "reason": "营养师已给出用餐调整建议" if status == "closed" else ("超时未接入" if status == "timeout" else None),
                          "rating": rating,
                          "comment": rng.choice(["回复很详细，按建议调整后感觉好多了", "营养师很有耐心，给了具体做法",
                                                 "建议比较实用，会继续执行", "希望能有更具体的食谱"]) if rating else None})
    w.bulk("consultation",
           "INSERT INTO consultation (user_id,dietitian_id,type,status,transferred_from_session_id,accepted_at,"
           "closed_at,close_reason,rating,rating_comment,created_at) VALUES (%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s)",
           ((c["uid"], c["did"], c["type"], c["status"], c["accepted"], c["closed"], c["reason"],
             c["rating"], c["comment"], c["created"]) for c in cons_rows), record_ids=True)

    cur.execute("SELECT id,user_id,dietitian_id,status,created_at FROM consultation WHERE id > %s ORDER BY id",
                (baseline["consultation"],))
    consultations = cur.fetchall()
    cm_rows = []
    for cid, uid, did, status, created in consultations:
        n = rng.randint(3, 12) if status != "pending" else rng.randint(1, 2)
        cm_rows.append((cid, "user", "text", f"营养师您好，我最近{ rng.choice(['血压偏高', '血糖控制不理想', '血脂检查异常', '想控制体重', '经常腹胀没胃口']) }，饮食上该怎么调整？", None, created + timedelta(minutes=1)))
        if status != "pending":
            for k in range(n - 1):
                who = "dietitian" if k % 2 == 0 else "user"
                content = rng.choice(DIETITIAN_REPLIES) if who == "dietitian" else rng.choice([
                    "好的，我试试这样调整。", "那早餐具体吃多少？", "这样吃会不会饿？", "明白了，谢谢营养师。"])
                cm_rows.append((cid, who, "text", content, None, created + timedelta(minutes=5 + k * 6)))
    w.bulk("consultation_message",
           "INSERT INTO consultation_message (consultation_id,sender_type,content_type,content,attachment_url,created_at) "
           "VALUES (%s,%s,%s,%s,%s,%s)", cm_rows)
    print(f"[i] 咨询 {len(consultations):,} / 咨询消息 {len(cm_rows):,}")

    # ---------------- 8. 营养师-客户绑定 ---------------- #
    bind_customers = rng.sample(user_ids, min(args.consultations, len(user_ids)))
    bind_rows = []
    for i, cid in enumerate(bind_customers):
        did = approved_dietitians[i % len(approved_dietitians)]
        bound = rnd_dt(rng, PERIOD_START, PERIOD_END)
        unbound = rng.random() < 0.12
        bind_rows.append((did, cid, rng.choices(["invite_code", "consultation", "admin"], weights=[54, 36, 10])[0],
                          "unbound" if unbound else "active", bound,
                          bound + timedelta(days=rng.randint(10, 120)) if unbound else None, bound))
    w.bulk("dietitian_customer",
           "INSERT INTO dietitian_customer (dietitian_id,customer_id,bind_source,status,bound_at,"
           "unbound_at,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
           ((b[0], b[1], b[2], b[3], b[4], b[5], b[6]) for b in bind_rows),
           record_ids=True)
    print(f"[i] 绑定关系 {len(bind_rows):,}")

    # ---------------- 9. 打卡 / 通知 ---------------- #
    checkin_users = rng.sample(profile_users, int(len(profile_users) * 0.55))
    ck_rows = []
    seen_ck = set()
    span = (PERIOD_END - PERIOD_START).days
    attempts = 0
    while len(ck_rows) < args.checkins and attempts < args.checkins * 12:
        attempts += 1
        uid = rng.choice(checkin_users)
        d = PERIOD_START + timedelta(days=rng.randint(0, span))
        ctype = rng.choices(["plan", "meal", "exercise", "weight"], weights=[46, 34, 14, 6])[0]
        key = (uid, d, ctype)
        if key in seen_ck:
            continue
        seen_ck.add(key)
        note = rng.choice(["按方案执行", "今天外出就餐，尽量选了清淡的", "完成了餐次打卡", "运动 30 分钟",
                           "体重已记录", "有轻微不适，已联系营养师"])
        ck_rows.append((uid, None, d, ctype, note, datetime.combine(d, time(rng.randint(6, 22), rng.randint(0, 59)))))
    w.bulk("checkin_record", "INSERT INTO checkin_record (user_id,plan_id,checkin_date,check_type,note,created_at) "
                             "VALUES (%s,%s,%s,%s,%s,%s)", ck_rows)
    print(f"[i] 打卡 {len(ck_rows):,}")

    notif_rows = []
    for i in range(args.notifications):
        uid = rng.choice(user_ids)
        ntype = rng.choices(["system", "checkin", "consultation", "risk"], weights=[40, 30, 20, 10])[0]
        if ntype == "risk":
            title = "健康风险提醒：建议尽快复核"
            content = "系统检测到您的最近一次测评等级为待改善，建议查看报告并咨询营养师。"
            link = "#report"
        elif ntype == "checkin":
            title = "打卡提醒"
            content = "今天的食养方案还没打卡，坚持记录更有助于营养师调整方案。"
            link = "#diet"
        elif ntype == "consultation":
            title = "营养师回复了您的咨询"
            content = "您的咨询有了新的回复，点此查看。"
            link = "#nutrition"
        else:
            title = "系统通知"
            content = rng.choice(["平台新增《成人糖尿病食养指南2023》要点解读", "食养方案生成完成",
                                  "您的健康档案已更新", "本周营养知识：全谷物怎么吃更合适"])
            link = "#home"
        created = rnd_dt(rng, PERIOD_START, PERIOD_END)
        read_at = created + timedelta(hours=rng.randint(1, 72)) if rng.random() < 0.68 else None
        notif_rows.append((uid, ntype, title, content, link, read_at, created))
    w.bulk("notification", "INSERT INTO notification (user_id,type,title,content,link,read_at,created_at) "
                           "VALUES (%s,%s,%s,%s,%s,%s,%s)", notif_rows)
    print(f"[i] 通知 {len(notif_rows):,}")

    # ---------------- 10. 操作日志 ---------------- #
    actions = [
        ("template.audit", "assessment_template", ["审核通过", "驳回并要求修改权重配置"]),
        ("user.disable", "sys_user", ["违规内容，暂时禁用", "账号异常登录，先行禁用"]),
        ("user.enable", "sys_user", ["申诉通过，恢复使用"]),
        ("dietitian.audit", "dietitian_profile", ["资质材料齐全，审核通过", "证书有效期临近，予以通过并提醒更新"]),
        ("config.update", "system_config", ["按营养师顾问意见调整维度权重", "调整评分等级阈值"]),
        ("data.export", "assessment_report", ["导出月度报告汇总", "导出慢病人群名单用于随访"]),
        ("dashboard.update", "dashboard_layout", ["调整运营大屏组件", "新增高风险人群组件"]),
        ("knowledge.rebuild", "knowledge_files", ["重建知识库索引"]),
    ]
    log_rows = []
    for i in range(args.oplogs):
        action, target, notes = rng.choice(actions)
        op = admin_id if rng.random() < 0.8 else rng.choice(dietitian_ids)
        created = rnd_dt(rng, PERIOD_START, PERIOD_END, hour_bias=[1, 0, 0, 0, 0, 1, 2, 4, 8, 12, 11, 9, 6, 7, 9, 10, 11, 10, 8, 6, 4, 3, 2, 1])
        log_rows.append((op, "admin" if op == admin_id else "dietitian", action, target,
                         str(rng.randint(1, 20000)), json.dumps({"note": rng.choice(notes)}, ensure_ascii=False),
                         f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}", created))
    w.bulk("operation_log", "INSERT INTO operation_log (operator_id,operator_role,action,target_type,target_id,"
                            "content,ip_address,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", log_rows)
    print(f"[i] 操作日志 {len(log_rows):,}")

    # ---------------- 11. 场景：宴请 / 旅行 / OCR / Text2SQL / 舌象 ---------------- #
    banquet_rows = []
    guest_rows = []
    for i in range(args.banquets):
        uid = rng.choice(user_ids)
        n = rng.randint(2, 10)
        people = rng.randint(n, n + 4)
        menu = {"dishes": []}
        for _ in range(rng.randint(6, 10)):
            m = rng.choice(materials)
            menu["dishes"].append({"name": f"{m['name']}{rng.choice(['炖鸡', '蒸鱼', '排骨汤', '炒时蔬', '小米粥', '煮水', '焖饭', '煎饼'])}",
                                   "main_material": m["name"], "efficacy": m["effects"],
                                   "caution": m["caution"]})
        title = rng.choice(["家庭聚餐", "朋友小聚", "长辈寿宴", "节日家宴", "同事聚餐"])
        created = rnd_dt(rng, PERIOD_START, PERIOD_END)
        banquet_rows.append((uid, title, created.date() + timedelta(days=rng.randint(1, 20)), people,
                             rng.choice([300, 500, 800, 1000, 1500, 2000]),
                             json.dumps({"预算": "人均 %d 元" % rng.choice([60, 80, 100, 150, 200]),
                                         "口味": rng.choice(["清淡为主", "少油少盐", "兼顾辣味"]),
                                         "忌口": rng.choice([[], ["海鲜"], ["花生"], ["辛辣"]])}, ensure_ascii=False),
                             json.dumps(menu, ensure_ascii=False),
                             rng.choices(["draft", "confirmed"], weights=[62, 38])[0], created))
    w.bulk("banquet_menu",
           "INSERT INTO banquet_menu (user_id,title,party_date,people_count,budget,constraints,dishes,status,created_at) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", banquet_rows, record_ids=True)
    cur.execute("SELECT id FROM banquet_menu WHERE id > %s ORDER BY id", (baseline["banquet_menu"],))
    menus = [r[0] for r in cur.fetchall()]
    for mid in menus:
        for _ in range(rng.randint(2, 4)):
            g = rng.choice(user_ids)
            ctx = user_ctx.get(g, {})
            guest_rows.append((mid, rng.choice(["张先生", "李女士", "王阿姨", "赵叔叔", "小陈", "刘姐", "老周"]),
                               json.dumps(rng.sample(DISEASE_POOL, rng.randint(0, 2)), ensure_ascii=False),
                               json.dumps(ctx.get("allergy", []), ensure_ascii=False),
                               rng.choice(list(CONSTITUTIONS.keys()))))
    w.bulk("banquet_guest", "INSERT INTO banquet_guest (menu_id,name,chronic_tags,allergies,constitution) "
                            "VALUES (%s,%s,%s,%s,%s)", guest_rows)
    print(f"[i] 宴请 {len(menus):,} / 宾客 {len(guest_rows):,}")

    travel_rows = []
    for i in range(args.travels):
        uid = rng.choice(user_ids)
        city, cuisine, tip = rng.choice(CITIES)
        ctx = user_ctx.get(uid, {})
        start = rnd_dt(rng, PERIOD_START, PERIOD_END).date()
        days = rng.randint(2, 8)
        travel_rows.append((uid, city, start, start + timedelta(days=days),
                            json.dumps({"慢病": ctx.get("diseases", []), "忌口": ctx.get("taboo", []),
                                        "随身药物": rng.choice([[], [], ["降压药"], ["降糖药"], ["肠胃药"]])},
                                       ensure_ascii=False),
                            json.dumps({"cuisine": cuisine, "advice": [tip,
                                        "旅途中优先选择蒸煮类菜品，主动要求少油少盐",
                                        "每日保证饮水 1500-2000 毫升，避免含糖饮料",
                                        "若在外就餐，可用清水涮去部分油脂"]}, ensure_ascii=False),
                            rng.choices(["planned", "ongoing", "done"], weights=[46, 12, 42])[0],
                            rnd_dt(rng, PERIOD_START, PERIOD_END)))
    w.bulk("travel_plan", "INSERT INTO travel_plan (user_id,destination,start_date,end_date,health_notes,advice,"
                          "status,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", travel_rows)
    print(f"[i] 旅行计划 {len(travel_rows):,}")

    ocr_rows = []
    for i in range(args.ocrs):
        uid = rng.choice(user_ids)
        created = rnd_dt(rng, PERIOD_START, PERIOD_END)
        name = rng.choice(["体检报告", "血常规", "生化全套", "门诊病历", "出院小结"]) + f"_{created:%Y%m%d}.jpg"
        ocr_rows.append((uid, name, "minio://ocr/%s" % name.lower().replace("_", "-"), "uploaded", None, created))
    w.bulk("ocr_report", "INSERT INTO ocr_report (user_id,file_name,file_url,status,raw_text,note,created_at) "
                         "VALUES (%s,%s,%s,%s,%s,%s,%s)",
           ((r[0], r[1], r[2], r[3], r[4], "OCR 引擎待接入，当前仅保留上传记录", r[5]) for r in ocr_rows),
           record_ids=True)
    cur.execute("SELECT id FROM ocr_report WHERE id > %s ORDER BY id", (baseline["ocr_report"],))
    ocr_ids = [r[0] for r in cur.fetchall()]
    ind_rows = []
    for oid in ocr_ids:
        for name, unit, ref, (lo, hi) in rng.sample(OCR_INDICATORS, rng.randint(5, 8)):
            v = round(rng.uniform(lo, hi), 1)
            abnormal = 1 if (v < lo * 1.02 or v > hi * 0.98) and rng.random() < 0.45 else 0
            ind_rows.append((oid, name, str(v), unit, ref, abnormal))
    w.bulk("ocr_indicator", "INSERT INTO ocr_indicator (report_id,name,value,unit,ref_range,abnormal) "
                            "VALUES (%s,%s,%s,%s,%s,%s)", ind_rows)
    print(f"[i] OCR 报告 {len(ocr_ids):,} / 指标 {len(ind_rows):,}")

    t2s_rows = []
    questions = [
        "统计上个月新增注册用户数", "查询本周完成测评的人数", "列出高血压人群的年龄段分布",
        "统计各模板的使用次数", "查询近 30 天高风险报告数量", "列出没有绑定营养师的客户",
        "统计营养师平均满意度", "查询每天的新增咨询量", "统计各类打卡类型的占比",
        "查询知识库中被引用最多的文档", "列出待审核的测评模板", "统计用户平均 BMI",
    ]
    for i in range(args.text2sql):
        q = rng.choice(questions)
        op = admin_id if rng.random() < 0.75 else rng.choice(dietitian_ids)
        status = rng.choices(["ok", "unsupported", "failed"], weights=[84, 10, 6])[0]
        t2s_rows.append((op, q, None if status != "ok" else "SELECT ... -- 受控模板生成，已注入行级权限条件",
                         "管理员全量 / 营养师仅本人绑定客户" if op != admin_id else "管理员行级权限：全量",
                         status, rng.randint(1, 400) if status == "ok" else 0,
                         rnd_dt(rng, PERIOD_START, PERIOD_END)))
    w.bulk("text2sql_query", "INSERT INTO text2sql_query (user_id,question,sql_text,row_scope,status,row_count,created_at) "
                             "VALUES (%s,%s,%s,%s,%s,%s,%s)", t2s_rows)
    print(f"[i] Text2SQL 记录 {len(t2s_rows):,}")

    tongue_rows = []
    for i in range(320):
        uid = rng.choice(user_ids)
        created = rnd_dt(rng, PERIOD_START, PERIOD_END)
        tongue_rows.append((uid, None, "minio://tongue/%s.jpg" % ("".join(rng.choice(string.hexdigits[:16]) for _ in range(12))),
                            rng.choices(["uploaded", "failed"], weights=[88, 12])[0], None, created))
    w.bulk("tongue_image", "INSERT INTO tongue_image (user_id,record_id,image_url,status,recognized_result,model_name,created_at) "
                           "VALUES (%s,%s,%s,%s,%s,NULL,%s)", tongue_rows, record_ids=True)

    # ---------------- 12. 报告批注 / 注销任务 / 配置 / 来源登记 ---------------- #
    ann_rows = []
    ann_targets = rng.sample(complete_reports, min(1800, len(complete_reports)))
    for rep_id, uid, rec_id, rep_tid in ann_targets:
        ann_rows.append((rep_id, rng.choice(approved_dietitians),
                         rng.choice(["已查看报告，建议先把主食换成杂粮并控制总量，两周后复测。",
                                     "报告中膳食结构维度偏低，可增加深色蔬菜与全谷物。",
                                     "用户指标波动较大，建议同步就医，食养方案作为辅助。",
                                     "已电话沟通，用户接受调整方案，继续随访。"]),
                         rnd_dt(rng, PERIOD_START, PERIOD_END)))
    w.bulk("report_annotation", "INSERT INTO report_annotation (report_id,dietitian_id,content,created_at,updated_at) "
                                "VALUES (%s,%s,%s,%s,%s)",
           ((a[0], a[1], a[2], a[3], a[3]) for a in ann_rows))
    print(f"[i] 报告批注 {len(ann_rows):,}")

    del_rows = []
    for i in range(45):
        uid = rng.choice(user_ids)
        created = rnd_dt(rng, PERIOD_START, PERIOD_END)
        state = rng.choices(["done", "pending", "running", "failed"], weights=[72, 16, 9, 3])[0]
        del_rows.append((uid, state, rng.choice(["mysql", "vector", "minio"]), created + timedelta(days=15),
                         created + timedelta(days=15, hours=2) if state == "done" else None,
                         "向量集合连接超时，稍后重试" if state == "failed" else None, created))
    w.bulk("data_deletion_task", "INSERT INTO data_deletion_task (user_id,state,scope,execute_at,finished_at,error,created_at) "
                                 "VALUES (%s,%s,%s,%s,%s,%s,%s)", del_rows)

    cfg_rows = [
        ("scoring.weights", json.dumps(SCORING_WEIGHTS, ensure_ascii=False),
         "各模板维度权重（营养师顾问确认后维护，改配置不改代码）"),
        ("scoring.thresholds", json.dumps(SCORING_THRESHOLDS, ensure_ascii=False),
         "评分等级阈值（百分制，从高到低匹配）"),
        ("scoring.version", SCORING_VERSION, "评分规则版本号，写入报告便于追溯"),
        ("chat.min_similarity", "0.55", "RAG 检索相似度阈值，低于该值视为知识库无相关内容并拒答"),
        ("agent.dispatch.fallback", "dietitian", "调度无关键词命中时的兜底子智能体"),
    ]
    w.bulk("system_config", "INSERT INTO system_config (config_key,config_value,description,updated_by,updated_at) "
                            "VALUES (%s,%s,%s,%s,NOW()) ON DUPLICATE KEY UPDATE config_value=VALUES(config_value),"
                            "description=VALUES(description),updated_by=VALUES(updated_by),updated_at=NOW()",
           ((k, v, d, admin_id) for k, v, d in cfg_rows))

    total = sum(w.counts.values())
    cur.execute(
        "INSERT INTO dataset_provenance (run_id,source_type,generator_version,period_start,period_end,status,"
        "row_counts,parameters_json,checksum_sha256,created_at,completed_at) "
        "VALUES (%s,'synthetic',%s,%s,%s,'complete',%s,%s,%s,NOW(),NOW())",
        (run_id, GEN_VERSION, PERIOD_START, PERIOD_END, json.dumps(dict(w.counts), ensure_ascii=False),
         json.dumps(plan_scale, ensure_ascii=False), "seed"))
    cur.execute("INSERT INTO system_config (config_key,config_value,description,updated_by,updated_at) "
                "VALUES (%s,%s,%s,%s,NOW()) ON DUPLICATE KEY UPDATE config_value=VALUES(config_value),updated_at=NOW()",
                (RUN_MARKER_KEY, json.dumps({"run_id": run_id, "baseline": baseline, "ranges": w.ranges},
                                            ensure_ascii=False),
                 "最后一次批量导入的基线快照（供 --purge 精确回滚，勿手工修改）", admin_id))
    conn.commit()
    cur.close()

    print("\n=== 基线自检（核对本批实际写入量）===")
    check = conn.cursor()
    all_ok = True
    for table in PURGE_ORDER:
        check.execute(f"SELECT COUNT(*) FROM {table} WHERE id > %s", (baseline[table],))
        got = check.fetchone()[0]
        want = w.counts.get(table, 0)
        ok = got == want
        all_ok = all_ok and ok
        print(f"  {table:22s} 实际 {got:>9,} / 登记 {want:>9,}  {'OK' if ok else '!! 不一致'}")
    check.close()
    if not all_ok:
        print("[!] 存在不一致项，请人工核对后再决定是否 --purge")

    print("\n=== 导入完成 ===")
    for k, v in sorted(w.counts.items(), key=lambda x: -x[1]):
        print(f"  {k:24s} {v:>9,}")
    print(f"  {'合计':24s} {total:>8,} 行（另含模板与配置）")
    print(f"  run_id = {run_id}")
    return 0


# 以下辅助函数在 main 之后使用（保持 main 可读性）
def template_map_by_id(tid, template_map, tpl_pool):
    for code, (t_id, ver, dims, qs) in template_map.items():
        if t_id == tid:
            return dims, qs
    return tpl_pool[0][2], tpl_pool[0][3]


def code_by_id(tid, template_map):
    for code, (t_id, ver, dims, qs) in template_map.items():
        if t_id == tid:
            return code
    return "UNKNOWN"


def build_analysis(res, weak, principles, ctx, rng):
    """用真实食养原则拼装分析文本（规则拼装，非 LLM 生成）。"""
    total = res["score"]
    lines = ["### 总体评价", "",
             f"本次测评总分 {total} 分，属于{res['level_name']}水平。"
             + ("各维度表现均衡，说明日常饮食与生活方式整体较为合理。" if total >= 80 else
                "部分维度仍有改善空间，建议按下方提示逐项调整，不必追求一次全部做到。"), "",
             "### 薄弱维度解析", ""]
    for d in weak:
        lines.append(f"- **{d['name']}**：得分 {d['score']}（{d['level_name']}）。"
                     f"该维度在本次测评中相对靠后，是近期可以优先改善的方向。")
    if principles:
        lines += ["", "### 可执行的生活与饮食建议", ""]
        idx = 1
        for name, rule in principles:
            lines.append(f"{idx}. **针对{name}**：")
            for p in rule["principles"]:
                lines.append(f"   - {p}")
            lines.append(f"   - 日常可参考的搭配：{rule['foods']}。")
            idx += 1
    else:
        lines += ["", "### 可执行的生活与饮食建议", "",
                  "1. 主食粗细搭配：用全谷物、杂豆替代部分精米白面，先吃蔬菜再吃主食。",
                  "2. 保证蔬菜水果摄入，每日蔬菜 300-500 克，深色蔬菜占一半以上。",
                  "3. 烹调以蒸煮炖为主，控制每日食盐不超过 5 克、烹调油 25-30 克。"]
    if ctx.get("bmi"):
        lines += [f"", f"注：按您填写的身高体重，BMI 约为 {ctx['bmi']}，"
                       + ("处于正常范围。" if 18.5 <= ctx["bmi"] < 24 else "建议纳入体重管理关注范围。")]
    lines += ["", "以上内容为一般性膳食建议，不能替代医师诊疗方案；指标异常请及时就医。"]
    return "\n".join(lines)


def build_plan_summary(cons, diseases, period, scenario):
    span = "四周" if period == "month" else "一周"
    base = f"本方案以{cons}为方向，结合{span}的膳食安排给出可执行的搭配建议。"
    if diseases:
        base += f"重点关注：{'、'.join(diseases)}。"
    base += {"控糖": "主食以低升糖的全谷物、杂豆为主，先菜后饭，控制总能量与餐次分配。",
             "高血压": "以低钠高钾为原则，蔬菜与薯类充足，烹调少盐，避免腌制与加工食品。",
             "痰湿": "以健脾利湿为原则，多用薏米、赤小豆、冬瓜等，减少甜腻与油炸食品。",
             "通用": "荤素搭配、粗细搭配，保证蔬菜水果与优质蛋白，逐步替换精制主食。"}[scenario]
    return base + " 若正在服药或有明确的医疗方案，请以主治医师意见为准。"


def pick_plan_refs(kb_by_key, diseases, rng):
    refs = []
    for d in diseases:
        rule = CHRONIC_RULES.get(d)
        if rule and rule["guide"] in kb_by_key:
            refs.append({"title": kb_by_key[rule["guide"]]["file_name"], "source": rule["guide"]})
    if not refs:
        key = rng.choice(["药食同源目录常识", "常见食材的性味与食养作用（常识）", "常见慢病的饮食一般原则（常识）"])
        if key in kb_by_key:
            refs.append({"title": kb_by_key[key]["file_name"], "source": key})
    return refs


def build_chat_answer(title, kb_by_key, rng):
    """按问题关键词给出真实知识片段支撑的回答（模板拼装，非 LLM）。"""
    matched = None
    for d in kb_by_key.values():
        for kw in ("糖尿病", "高血压", "高尿酸", "痛风", "高脂", "肥胖", "肾脏", "儿童", "生长迟缓", "慢性肾脏"):
            if kw in title and kw in d["source"]:
                matched = d
                break
        if matched:
            break
    if not matched:
        for key in ("常见食材的性味与食养作用（常识）", "药食同源目录常识", "常见慢病的饮食一般原则（常识）",
                    "食材搭配与食用禁忌常识"):
            if key in kb_by_key:
                matched = kb_by_key[key]
                break
    frag = rng.choice(matched["fragments"]) if matched else "该问题超出知识库范围。"
    body = (f"关于「{title}」，可以分两步看：\n\n"
            f"1）一般原则：{frag.strip()[:180]}\n\n"
            f"2）落到日常：建议先固定一日三餐的时间与总量，把主食的一部分换成全谷物或杂豆，"
            f"蔬菜保证每天 300-500 克，烹调以蒸煮炖为主，食盐控制在每天 5 克以内。\n\n"
            f"以上是一般性膳食参考，不能替代医师诊疗；如果指标持续异常或正在服药，请及时就医。")
    refs = [{"score": round(rng.uniform(0.58, 0.88), 3), "title": matched["file_name"] if matched else "",
             "source": matched["source"] if matched else "", "fragment": frag}] if matched else []
    return body, refs


def followup_question(rng, title):
    return rng.choice([f"那{title[:6]}具体每天吃多少合适？", "有没有更简单一点的做法？",
                       "这种情况需要吃多长时间？", "如果同时在吃降压药要注意什么？",
                       "有没有推荐的食谱可以照着做？"])


if __name__ == "__main__":
    raise SystemExit(main())
