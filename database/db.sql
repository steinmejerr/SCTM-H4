-- CREATE TABLE
CREATE TABLE IF NOT EXISTS routes (
    routeid INT AUTO_INCREMENT PRIMARY KEY,
    route VARCHAR(255) NOT NULL
);

CREATE TABLE IF NOT EXISTS cars (
    carid INT AUTO_INCREMENT PRIMARY KEY,
    speed INT NOT NULL,
    routeid INT NOT NULL,
    directionForward BOOL NOT NULL DEFAULT TRUE,
    timestamp DATETIME NOT NULL,
    FOREIGN KEY (routeid) REFERENCES routes(routeid)
);


-- INSERT INTO 
INSERT INTO routes (route)
VALUES 
('Ringstedvej'),
('Sorøvej'),
('Slagelsevej');
