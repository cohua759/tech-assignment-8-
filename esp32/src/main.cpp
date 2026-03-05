#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <Adafruit_AMG88xx.h>
#include <TensorFlowLite_ESP32.h>
#include "tensorflow/lite/micro/all_ops_resolver.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_error_reporter.h"
#include "tensorflow/lite/schema/schema_generated.h"
#include "ECE140_WIFI.h"
#include "ECE140_MQTT.h"
#include "model_data.h"
#include "model_params.h"

//mqtt topics
constexpr const char* CLIENT_ID    = "cohua759-ta7-esp32";
constexpr const char* TOPIC_PREFIX = "cohua759/ta7";
constexpr const char* TOPIC_SUFFIX = "thermal";

Adafruit_AMG88xx amg;
float pixels[AMG88xx_PIXEL_ARRAY_SIZE];
float features[N_FEATURES];   

ECE140_WIFI wifi;
ECE140_MQTT mqtt(CLIENT_ID, TOPIC_PREFIX);

const char* ucsdUsername              = UCSD_USERNAME;
String      ucsdPassword              = String(UCSD_PASSWORD);
const char* wifiSsid                  = WIFI_SSID;
const char* nonEnterpriseWifiPassword = NON_ENTERPRISE_WIFI_PASSWORD;

enum Mode { IDLE, GET_ONE, CONTINUOUS };
volatile Mode currentMode = IDLE;
unsigned long lastPublish  = 0;
const unsigned long INTERVAL = 1000;

constexpr int kArenaSize = 32 * 1024;   
alignas(16) uint8_t tensor_arena[kArenaSize];

const tflite::Model*       model       = nullptr;
tflite::MicroInterpreter*  interpreter = nullptr;
TfLiteTensor*              input       = nullptr;
TfLiteTensor*              output      = nullptr;

static tflite::AllOpsResolver     resolver;
static tflite::MicroErrorReporter error_reporter;

//ta6 copy for confidence
int largestBlob(float grid[8][8], float threshold) {
    bool visited[8][8] = {};
    int largest = 0;
    int qr[64], qc[64];

    for (int r = 0; r < 8; r++) {
        for (int c = 0; c < 8; c++) {
            if (visited[r][c] || grid[r][c] <= threshold) continue;
            int size = 0, head = 0, tail = 0;
            qr[tail] = r; qc[tail] = c; tail++;
            visited[r][c] = true;
            while (head < tail) {
                int cr = qr[head], cc = qc[head]; head++;
                size++;
                const int dr[] = {-1, 1, 0, 0};
                const int dc[] = {0, 0, -1, 1};
                for (int d = 0; d < 4; d++) {
                    int nr = cr + dr[d], nc = cc + dc[d];
                    if (nr >= 0 && nr < 8 && nc >= 0 && nc < 8
                        && !visited[nr][nc] && grid[nr][nc] > threshold) {
                        visited[nr][nc] = true;
                        qr[tail] = nr; qc[tail] = nc; tail++;
                    }
                }
            }
            if (size > largest) largest = size;
        }
    }
    return largest;
}

void computeFeatures(float* raw_pixels, float* out_features) {
    float grid[8][8];
    for (int i = 0; i < 64; i++) grid[i / 8][i % 8] = raw_pixels[i];

    float sorted[64];
    memcpy(sorted, raw_pixels, 64 * sizeof(float));
    for (int i = 1; i < 64; i++) {
        float key = sorted[i]; int j = i - 1;
        while (j >= 0 && sorted[j] > key) { sorted[j+1] = sorted[j]; j--; }
        sorted[j+1] = key;
    }
    float median = (sorted[31] + sorted[32]) / 2.0f;
    float threshold = median + 3.0f;

    float sum_sq = 0.0f, row_min = raw_pixels[0], row_max = raw_pixels[0];
    int count_above_3 = 0, count_above_5 = 0, hot_count = 0;
    float hot_row_sum = 0.0f, hot_col_sum = 0.0f;

    for (int i = 0; i < 64; i++) {
        float diff = raw_pixels[i] - median;
        sum_sq += diff * diff;
        if (raw_pixels[i] < row_min) row_min = raw_pixels[i];
        if (raw_pixels[i] > row_max) row_max = raw_pixels[i];
        if (raw_pixels[i] > threshold) {
            count_above_3++;
            hot_row_sum += (float)(i / 8);
            hot_col_sum += (float)(i % 8);
            hot_count++;
        }
        if (raw_pixels[i] > median + 5.0f) count_above_5++;
    }
    float std_dev = sqrtf(sum_sq / 64.0f);
    if (std_dev < 0.1f) std_dev = 0.1f;

    for (int i = 0; i < 64; i++)
        out_features[i] = (raw_pixels[i] - median) / std_dev;

    out_features[64] = row_max;
    out_features[65] = row_max - row_min;
    out_features[66] = (float)count_above_3;
    out_features[67] = (float)count_above_5;

    float h_sum = 0.0f, v_sum = 0.0f;
    for (int r = 0; r < 8; r++)
        for (int c = 0; c < 7; c++) h_sum += fabsf(grid[r][c+1] - grid[r][c]);
    for (int r = 0; r < 7; r++)
        for (int c = 0; c < 8; c++) v_sum += fabsf(grid[r+1][c] - grid[r][c]);
    out_features[68] = (h_sum / 56.0f + v_sum / 56.0f) / 2.0f;

    out_features[69] = (float)largestBlob(grid, threshold);

    float q[4] = {0, 0, 0, 0};
    for (int r = 0; r < 4; r++) for (int c = 0; c < 4; c++) q[0] += grid[r][c];
    for (int r = 0; r < 4; r++) for (int c = 4; c < 8; c++) q[1] += grid[r][c];
    for (int r = 4; r < 8; r++) for (int c = 0; c < 4; c++) q[2] += grid[r][c];
    for (int r = 4; r < 8; r++) for (int c = 4; c < 8; c++) q[3] += grid[r][c];
    for (int i = 0; i < 4; i++) q[i] /= 16.0f;
    float q_mean = (q[0]+q[1]+q[2]+q[3]) / 4.0f, q_var = 0.0f;
    for (int i = 0; i < 4; i++) q_var += (q[i]-q_mean)*(q[i]-q_mean);
    out_features[70] = q_var / 4.0f;

    float center_sum = 0.0f, outer_sum = 0.0f; int outer_count = 0;
    for (int r = 0; r < 8; r++) {
        for (int c = 0; c < 8; c++) {
            if (r >= 2 && r < 6 && c >= 2 && c < 6) center_sum += grid[r][c];
            else { outer_sum += grid[r][c]; outer_count++; }
        }
    }
    out_features[71] = (center_sum / 16.0f) - (outer_sum / (float)outer_count);

    float row_maxes[8], col_maxes[8];
    for (int r = 0; r < 8; r++) {
        row_maxes[r] = grid[r][0];
        for (int c = 1; c < 8; c++) if (grid[r][c] > row_maxes[r]) row_maxes[r] = grid[r][c];
    }
    for (int c = 0; c < 8; c++) {
        col_maxes[c] = grid[0][c];
        for (int r = 1; r < 8; r++) if (grid[r][c] > col_maxes[c]) col_maxes[c] = grid[r][c];
    }
    float rm_mean = 0, cm_mean = 0;
    for (int i = 0; i < 8; i++) { rm_mean += row_maxes[i]; cm_mean += col_maxes[i]; }
    rm_mean /= 8.0f; cm_mean /= 8.0f;
    float rm_var = 0, cm_var = 0;
    for (int i = 0; i < 8; i++) {
        rm_var += (row_maxes[i]-rm_mean)*(row_maxes[i]-rm_mean);
        cm_var += (col_maxes[i]-cm_mean)*(col_maxes[i]-cm_mean);
    }
    out_features[72] = sqrtf(rm_var / 8.0f);
    out_features[73] = sqrtf(cm_var / 8.0f);

    if (hot_count > 0) {
        float cr = hot_row_sum / (float)hot_count;
        float cc = hot_col_sum / (float)hot_count;
        out_features[74] = sqrtf((cr-3.5f)*(cr-3.5f) + (cc-3.5f)*(cc-3.5f));
    } else {
        out_features[74] = 0.0f;
    }
    out_features[75] = (float)count_above_3 / 64.0f;

    for (int i = 0; i < N_FEATURES; i++)
        out_features[i] = (out_features[i] - SCALER_MEAN[i]) / SCALER_SCALE[i];
}

//ta6 copy
float runInference(float* scaled_features) {
    float input_scale      = input->params.scale;
    int   input_zero_point = input->params.zero_point;
    int8_t* input_data     = input->data.int8;

    for (int i = 0; i < N_FEATURES; i++) {
        int val = (int)roundf(scaled_features[i] / input_scale) + input_zero_point;
        if (val < -128) val = -128;
        if (val > 127)  val = 127;
        input_data[i] = (int8_t)val;
    }

    interpreter->Invoke();

    float output_scale      = output->params.scale;
    int   output_zero_point = output->params.zero_point;
    int8_t raw_output       = output->data.int8[0];
    return (raw_output - output_zero_point) * output_scale;
}

//recieving messages callback function
void mqttCallback(char* topic, uint8_t* payload, unsigned int length) {
    String message = "";
    for (unsigned int i = 0; i < length; i++) message += (char)payload[i];
    Serial.print("[MQTT] Received: "); Serial.println(message);

    if (message.indexOf("pixels") != -1) return; 

    String command = message;
    int cmdIdx = message.indexOf("\"command\"");
    if (cmdIdx >= 0) {
        int colon = message.indexOf(":", cmdIdx);
        int q1    = message.indexOf("\"", colon);
        int q2    = message.indexOf("\"", q1 + 1);
        if (q1 >= 0 && q2 > q1) command = message.substring(q1 + 1, q2);
    }
    Serial.print("[CMD] "); Serial.println(command);

    if      (command.indexOf("get_one") != -1)          { currentMode = GET_ONE;    Serial.println("[STATE] GET_ONE"); }
    else if (command.indexOf("start_continuous") != -1) { currentMode = CONTINUOUS; Serial.println("[STATE] CONTINUOUS"); }
    else if (command.indexOf("stop") != -1)             { currentMode = IDLE;       Serial.println("[STATE] IDLE"); }
}

void publishReading() {
    amg.readPixels(pixels);
    float thermistor = amg.readThermistor();

    computeFeatures(pixels, features);
    float confidence  = runInference(features);

    String prediction = (confidence >= 0.5f) ? "PRESENT" : "EMPTY";

    String mac = WiFi.macAddress();
    String msg = "{\"mac_address\":\"" + mac + "\",";
    msg += "\"thermistor\":"   + String(thermistor, 2) + ",";
    msg += "\"prediction\":\"" + prediction + "\",";
    msg += "\"confidence\":"   + String(confidence, 4) + ",";
    msg += "\"pixels\":[";
    for (int i = 0; i < AMG88xx_PIXEL_ARRAY_SIZE; i++) {
        msg += String(pixels[i], 2);
        if (i < AMG88xx_PIXEL_ARRAY_SIZE - 1) msg += ",";
    }
    msg += "]}";

    mqtt.publishMessage(TOPIC_SUFFIX, msg);
    Serial.printf("[Thermal] %s | conf:%.4f | therm:%.1fC\n",
                  prediction.c_str(), confidence, thermistor);
}

void setup() {
    Serial.begin(115200);
    delay(2000);

    if (strlen(nonEnterpriseWifiPassword) < 2)
        wifi.connectToWPAEnterprise(wifiSsid, ucsdUsername, ucsdPassword.c_str());
    else
        wifi.connectToWiFi("RESNET-GUEST-DEVICE", nonEnterpriseWifiPassword);

    Serial.print("[WiFi] MAC: "); Serial.println(WiFi.macAddress());
    Serial.print("[MQTT] Topic: ");
    Serial.print(TOPIC_PREFIX); Serial.print("/"); Serial.println(TOPIC_SUFFIX);

    mqtt.connectToBroker();
    delay(100);
    mqtt.setCallback(mqttCallback);
    mqtt.subscribeTopic(TOPIC_SUFFIX);

    Wire.begin();
    if (!amg.begin()) {
        Serial.println("[ERROR] AMG8833 not found");
        while (1) delay(1000);
    }
    delay(100);

    model = tflite::GetModel(model_tflite);
    static tflite::MicroInterpreter static_interpreter(
        model, resolver, tensor_arena, kArenaSize, &error_reporter);
    interpreter = &static_interpreter;
    interpreter->AllocateTensors();
    input  = interpreter->input(0);
    output = interpreter->output(0);

    Serial.printf("[TFLite] Arena used: %d / %d bytes\n",
                  interpreter->arena_used_bytes(), kArenaSize);
    Serial.printf("[DEBUG] input type=%d | scale=%.6f | zp=%d\n",
                  input->type, input->params.scale, input->params.zero_point);
    Serial.printf("[DEBUG] output type=%d | scale=%.6f | zp=%d\n",
                  output->type, output->params.scale, output->params.zero_point);

    Serial.println("[READY] Waiting for: get_one / start_continuous / stop");
}

void loop() {
    mqtt.loop();

    switch (currentMode) {
        case GET_ONE:
            publishReading();
            currentMode = IDLE;
            break;
        case CONTINUOUS:
            if (millis() - lastPublish >= INTERVAL) {
                publishReading();
                lastPublish = millis();
            }
            break;
        case IDLE:
        default:
            break;
    }
}
