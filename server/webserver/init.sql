CREATE TABLE IF NOT EXISTS devices (
    id          SERIAL PRIMARY KEY,
    mac_address VARCHAR(17) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS readings (
    id              SERIAL PRIMARY KEY,
    mac_address     VARCHAR(17) NOT NULL,
    thermistor_temp FLOAT NOT NULL,
    prediction      VARCHAR(10) NOT NULL,
    confidence      FLOAT NOT NULL,
    pixels          JSONB NOT NULL,
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (mac_address) REFERENCES devices(mac_address)
);
