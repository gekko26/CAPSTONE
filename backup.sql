mysqldump: Deprecated program name. It will be removed in a future release, use '/usr/bin/mariadb-dump' instead
/*M!999999\- enable the sandbox mode */ 
-- MariaDB dump 10.20-12.3.3-MariaDB, for Linux (x86_64)
--
-- Host: localhost    Database: Alcohol_System
-- ------------------------------------------------------
-- Server version	12.3.3-MariaDB

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*M!100616 SET @OLD_NOTE_VERBOSITY=@@NOTE_VERBOSITY, NOTE_VERBOSITY=0 */;

--
-- Table structure for table `deployment_logs`
--

DROP TABLE IF EXISTS `deployment_logs`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `deployment_logs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `date` timestamp NULL DEFAULT current_timestamp(),
  `subject_id` int(11) DEFAULT NULL,
  `mq3_1_max` float DEFAULT NULL,
  `mq3_1_avg` float DEFAULT NULL,
  `mq3_1_std` float DEFAULT NULL,
  `mq3_2_max` float DEFAULT NULL,
  `mq3_2_avg` float DEFAULT NULL,
  `mq3_2_std` float DEFAULT NULL,
  `mq3_3_max` float DEFAULT NULL,
  `mq3_3_avg` float DEFAULT NULL,
  `mq3_3_std` float DEFAULT NULL,
  `rise_time` float DEFAULT NULL,
  `decay_time` float DEFAULT NULL,
  `spatial_variance` float DEFAULT NULL,
  `spatial_variance_avg` float DEFAULT NULL,
  `breath_ratio` float DEFAULT NULL,
  `sanitizer_ratio` float DEFAULT NULL,
  `spatial_direction` float DEFAULT NULL,
  `temperature` float DEFAULT NULL,
  `humidity` float DEFAULT NULL,
  `prediction` varchar(20) DEFAULT NULL,
  `confidence` float DEFAULT NULL,
  `risk_level` varchar(20) DEFAULT NULL,
  `model_version` varchar(50) DEFAULT NULL,
  `estimated_bac` float DEFAULT NULL,
  `bac_tier` varchar(20) DEFAULT NULL,
  `denial_reason` varchar(20) DEFAULT NULL,
  `height_offset_cm` float DEFAULT NULL,
  `reading_id` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_deployment_logs_reading_id` (`reading_id`),
  KEY `fk_deployment_logs_subject` (`subject_id`),
  KEY `ix_deployment_logs_date` (`date`),
  CONSTRAINT `fk_deployment_logs_subject` FOREIGN KEY (`subject_id`) REFERENCES `subjects` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `deployment_logs`
--

SET @OLD_AUTOCOMMIT=@@AUTOCOMMIT, @@AUTOCOMMIT=0;
LOCK TABLES `deployment_logs` WRITE;
/*!40000 ALTER TABLE `deployment_logs` DISABLE KEYS */;
/*!40000 ALTER TABLE `deployment_logs` ENABLE KEYS */;
UNLOCK TABLES;
COMMIT;
SET AUTOCOMMIT=@OLD_AUTOCOMMIT;

--
-- Table structure for table `sensor_readings`
--

DROP TABLE IF EXISTS `sensor_readings`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `sensor_readings` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `subject_id` int(11) DEFAULT NULL,
  `temperature` float DEFAULT NULL,
  `humidity` float DEFAULT NULL,
  `bac` float DEFAULT NULL,
  `ear` float DEFAULT NULL,
  `label` varchar(20) DEFAULT NULL,
  `fusion_label` varchar(20) DEFAULT NULL,
  `model_used` varchar(50) DEFAULT NULL,
  `date` datetime DEFAULT current_timestamp(),
  `estimated_bac` float DEFAULT NULL,
  `bac_tier` varchar(20) DEFAULT NULL,
  `denial_reason` varchar(20) DEFAULT NULL,
  `height_offset_cm` float DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `subject_id` (`subject_id`),
  KEY `ix_sensor_readings_id` (`id`),
  KEY `ix_sensor_readings_date` (`date`),
  CONSTRAINT `sensor_readings_ibfk_1` FOREIGN KEY (`subject_id`) REFERENCES `subjects` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `sensor_readings`
--

SET @OLD_AUTOCOMMIT=@@AUTOCOMMIT, @@AUTOCOMMIT=0;
LOCK TABLES `sensor_readings` WRITE;
/*!40000 ALTER TABLE `sensor_readings` DISABLE KEYS */;
/*!40000 ALTER TABLE `sensor_readings` ENABLE KEYS */;
UNLOCK TABLES;
COMMIT;
SET AUTOCOMMIT=@OLD_AUTOCOMMIT;

--
-- Table structure for table `subjects`
--

DROP TABLE IF EXISTS `subjects`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `subjects` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  `face_id` varchar(255) DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT current_timestamp(),
  `group` varchar(30) DEFAULT 'Staff',
  `avatar_path` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_subjects_face_id` (`face_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `subjects`
--

SET @OLD_AUTOCOMMIT=@@AUTOCOMMIT, @@AUTOCOMMIT=0;
LOCK TABLES `subjects` WRITE;
/*!40000 ALTER TABLE `subjects` DISABLE KEYS */;
/*!40000 ALTER TABLE `subjects` ENABLE KEYS */;
UNLOCK TABLES;
COMMIT;
SET AUTOCOMMIT=@OLD_AUTOCOMMIT;

--
-- Table structure for table `training_data`
--

DROP TABLE IF EXISTS `training_data`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `training_data` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `date` timestamp NULL DEFAULT current_timestamp(),
  `subject_id` int(11) DEFAULT NULL,
  `mq3_1_max` float DEFAULT NULL,
  `mq3_1_avg` float DEFAULT NULL,
  `mq3_1_std` float DEFAULT NULL,
  `mq3_2_max` float DEFAULT NULL,
  `mq3_2_avg` float DEFAULT NULL,
  `mq3_2_std` float DEFAULT NULL,
  `mq3_3_max` float DEFAULT NULL,
  `mq3_3_avg` float DEFAULT NULL,
  `mq3_3_std` float DEFAULT NULL,
  `rise_time` float DEFAULT NULL,
  `decay_time` float DEFAULT NULL,
  `spatial_variance` float DEFAULT NULL,
  `spatial_variance_avg` float DEFAULT NULL,
  `temperature` float DEFAULT NULL,
  `humidity` float DEFAULT NULL,
  `bac` float DEFAULT NULL,
  `label` int(11) DEFAULT -1,
  `confidence` float DEFAULT NULL,
  `sub_label` varchar(20) DEFAULT NULL,
  `image_path` varchar(255) DEFAULT NULL,
  `mar` float DEFAULT NULL,
  `ear` float DEFAULT NULL,
  `head_pitch` float DEFAULT NULL,
  `breath_ratio` float DEFAULT NULL,
  `sanitizer_ratio` float DEFAULT NULL,
  `spatial_direction` float DEFAULT NULL,
  `trial_id` int(11) DEFAULT NULL,
  `auto_labeled` tinyint(1) DEFAULT 0,
  `label_confirmed` tinyint(1) DEFAULT 1,
  `height_offset_cm` float DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `fk_training_data_subject` (`subject_id`),
  KEY `ix_training_data_date` (`date`),
  KEY `ix_training_data_trial_id` (`trial_id`),
  CONSTRAINT `fk_training_data_subject` FOREIGN KEY (`subject_id`) REFERENCES `subjects` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `training_data`
--

SET @OLD_AUTOCOMMIT=@@AUTOCOMMIT, @@AUTOCOMMIT=0;
LOCK TABLES `training_data` WRITE;
/*!40000 ALTER TABLE `training_data` DISABLE KEYS */;
/*!40000 ALTER TABLE `training_data` ENABLE KEYS */;
UNLOCK TABLES;
COMMIT;
SET AUTOCOMMIT=@OLD_AUTOCOMMIT;

--
-- Table structure for table `users`
--

DROP TABLE IF EXISTS `users`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `users` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `username` varchar(50) NOT NULL,
  `hashpassword` varchar(255) DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT current_timestamp(),
  `updated_at` timestamp NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `username` (`username`)
) ENGINE=InnoDB AUTO_INCREMENT=19 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `users`
--

SET @OLD_AUTOCOMMIT=@@AUTOCOMMIT, @@AUTOCOMMIT=0;
LOCK TABLES `users` WRITE;
/*!40000 ALTER TABLE `users` DISABLE KEYS */;
INSERT INTO `users` VALUES
(1,'gonzagaedrian143','123','2026-02-10 13:40:00','2026-02-10 13:43:28'),
(2,'joms69','jom69','2026-02-10 13:44:57','2026-02-10 13:46:13'),
(3,'test@gmail.com','a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3','2026-02-13 05:05:41','2026-02-13 05:05:41'),
(17,'edrian','123','2026-02-14 05:44:36','2026-02-14 05:44:36'),
(18,'ag','123456','2026-02-19 02:28:07','2026-02-19 02:28:07');
/*!40000 ALTER TABLE `users` ENABLE KEYS */;
UNLOCK TABLES;
COMMIT;
SET AUTOCOMMIT=@OLD_AUTOCOMMIT;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*M!100616 SET NOTE_VERBOSITY=@OLD_NOTE_VERBOSITY */;

-- Dump completed on 2026-09-22 14:24:46
