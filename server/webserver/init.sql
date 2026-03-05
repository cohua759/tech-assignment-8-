CREATE TABLE IF NOT EXISTS devices (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    mac_address VARCHAR(17) UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS readings (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    mac_address     VARCHAR(17) NOT NULL,
    pixels          JSON NOT NULL,
    thermistor_temp DOUBLE,
    prediction      VARCHAR(10),
    confidence      DOUBLE,
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (mac_address) REFERENCES devices(mac_address)
);
