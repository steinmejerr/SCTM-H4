
CREATE TABLE IF NOT EXISTS `analytic_results` (
  `resultid` int(11) NOT NULL AUTO_INCREMENT,
  `recent_congestion` timestamp NOT NULL DEFAULT current_timestamp(),
  `average_speed` tinyint(4) NOT NULL,
  `max_speed` tinyint(4) NOT NULL,
  `min_speed` tinyint(4) NOT NULL,
  `total_vehicles` int(11) NOT NULL,
  `recent_accident_prone_road` varchar(255) NOT NULL,
  PRIMARY KEY (`resultid`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE IF NOT EXISTS `cars` (
  `speed` int(11) DEFAULT NULL,
  `routeid` int(11) DEFAULT NULL,
  `timestamp` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

CREATE TABLE IF NOT EXISTS `routes` (
  `routeid` int(11) NOT NULL AUTO_INCREMENT,
  `route` varchar(255) NOT NULL,
  PRIMARY KEY (`routeid`)
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;

INSERT INTO `routes` (`routeid`, `route`) VALUES
	(1, 'Ringstedvej'),
	(2, 'Sorøvej'),
	(3, 'Slagelsevej');