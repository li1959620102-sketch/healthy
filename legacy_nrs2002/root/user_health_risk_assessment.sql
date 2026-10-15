/*
SQLyog Community v13.1.6 (64 bit)
MySQL - 8.0.37 : Database - test_db
*********************************************************************
*/

/*!40101 SET NAMES utf8 */;

/*!40101 SET SQL_MODE=''*/;

/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;
CREATE DATABASE /*!32312 IF NOT EXISTS*/`test_db` /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci */ /*!80016 DEFAULT ENCRYPTION='N' */;

USE `test_db`;

/*Table structure for table `user_health_risk_assessment` */

DROP TABLE IF EXISTS `user_health_risk_assessment`;

CREATE TABLE `user_health_risk_assessment` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '记录ID',
  `user_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL COMMENT '用户唯一标识',
  `user_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL COMMENT '用户姓名',
  `sex` enum('男','女','其他') CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL COMMENT '性别',
  `age` tinyint unsigned NOT NULL COMMENT '年龄',
  `assessment_time` datetime NOT NULL COMMENT '测评时间',
  `assessment_count` int DEFAULT '1' COMMENT '测试次数（用户第几次测评）',
  `assessment_type` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci DEFAULT 'NRS2002' COMMENT '测评类型（NRS2002/MUST等）',
  `total_score` tinyint unsigned NOT NULL COMMENT '总分（0-7分）',
  `nutritional_impairment_score` tinyint unsigned NOT NULL COMMENT '营养受损分（0-3分）',
  `disease_severity_score` tinyint unsigned NOT NULL COMMENT '疾病严重度分（0-3分）',
  `age_score` tinyint unsigned NOT NULL COMMENT '年龄分（0-1分）',
  `assessment_basis` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci COMMENT '评分依据说明',
  `risk_level` enum('无风险','低风险','中风险','高风险') CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL COMMENT '风险等级',
  `recommendations` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci COMMENT '健康建议',
  `bmi` decimal(4,2) DEFAULT NULL COMMENT 'BMI指数',
  `weight_change` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '体重变化情况',
  `disease_condition` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci COMMENT '疾病状况描述',
  `dietary_intake` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '进食情况',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
  `created_by` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci DEFAULT 'system' COMMENT '创建人',
  PRIMARY KEY (`id`),
  KEY `idx_user_id` (`user_id`),
  KEY `idx_assessment_time` (`assessment_time`),
  KEY `idx_risk_level` (`risk_level`),
  KEY `idx_total_score` (`total_score`),
  KEY `idx_user_assessment` (`user_id`,`assessment_time`)
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='用户健康风险测评记录表';

/*Data for the table `user_health_risk_assessment` */

insert  into `user_health_risk_assessment`(`id`,`user_id`,`user_name`,`sex`,`age`,`assessment_time`,`assessment_count`,`assessment_type`,`total_score`,`nutritional_impairment_score`,`disease_severity_score`,`age_score`,`assessment_basis`,`risk_level`,`recommendations`,`bmi`,`weight_change`,`disease_condition`,`dietary_intake`,`created_at`,`updated_at`,`created_by`) values 
(1,'USER001','张三','男',72,'2024-01-15 10:30:00',1,'NRS2002',6,3,2,1,'1.营养受损：BMI17.8（＜18.5）→3分；2.疾病严重度：急性脑中风需鼻饲营养→2分；3.年龄72岁≥70→1分；总分3+2+1=6分','高风险','建议立即进行营养干预，考虑肠内营养支持，密切监测体重和营养指标',17.80,'近2个月体重下降12%','急性脑中风，需鼻饲营养','进食量减少＞75%','2025-09-24 09:40:43','2025-09-24 09:40:43','system'),
(2,'USER002','李四','女',65,'2024-01-15 14:20:00',1,'NRS2002',2,1,1,0,'1.营养受损：BMI19.2（18.5-20.4）→1分；2.疾病严重度：2型糖尿病稳定期→1分；3.年龄65岁＜70→0分；总分1+1+0=2分','低风险','建议定期监测营养状况，保持均衡饮食，适当运动',19.20,'近3个月体重下降4.6%','2型糖尿病稳定期','正常进食，无进食困难','2025-09-24 09:40:43','2025-09-24 09:40:43','system'),
(3,'USER003','王五','男',68,'2026-01-27 09:15:00',1,'NRS2002',4,2,2,0,'1.营养受损：BMI18.4（＜18.5）→2分；2.疾病严重度：胃癌术后化疗→2分；3.年龄68岁＜70→0分；总分2+2+0=4分','中风险','需要营养支持治疗，建议高蛋白高能量饮食，定期复查营养指标',18.40,'近1个月体重下降8%','胃癌术后化疗期','进食量减少60%','2025-09-24 09:40:43','2026-01-27 11:27:51','system');

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;
