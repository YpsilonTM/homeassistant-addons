
# ModQTT

ModQTT is a general-purpose Modbus TCP to MQTT bridge. It reads values from any Modbus TCP device (such as inverters, meters, sensors, or industrial equipment) and publishes them to MQTT topics for integration with Home Assistant or other systems. It is not limited to any specific device or manufacturer.


## Features

- Read Modbus input/holding registers from any Modbus TCP device
- Decode u16/s16/u32/s32/f32 with byte/word order control
- Publish to configurable MQTT topic prefix
- Optional write parameters with explicit safety flags


## Installation

1. In Home Assistant, go to Settings → Add-ons → Add-on Store.
2. Add this repository URL:
   - https://github.com/YpsilonTM/homeassistant-addons
3. Install the ModQTT add-on.


## Basic configuration

Switch the add-on options editor to YAML mode. The add-on configuration is a single YAML document with these top-level sections:

- `allow_writes`: keep `false` unless you intentionally enable writes
- `modbus_host`, `modbus_port`, `modbus_unit_id`, `modbus_timeout_seconds`, `poll_interval_seconds`
- `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password`, `mqtt_client_id`
- `topic_prefix`, `availability_topic`, `retain_state`, `json_grouped_topics`, `discovery_enabled`
- `readings`: list of read-only or published register definitions
- `write_parameters`: optional list of writable holding-register definitions

Minimal example:

```yaml
allow_writes: false
modbus_host: 192.168.1.10
modbus_port: 502
modbus_unit_id: 1
modbus_timeout_seconds: 3.0
poll_interval_seconds: 5.0
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: ""
mqtt_password: ""
mqtt_client_id: modqtt-addon
topic_prefix: prod/device
availability_topic: bridge/availability
retain_state: true
json_grouped_topics: false
discovery_enabled: false
readings:
   - name: grid_power
      topic_suffix: grid_power
      register_type: input
      address: 13033
      length_words: 2
      data_type: s32
      scale: 1.0
      offset: 0
      decimals: 0
      byte_order: big
      word_order: little
      unit: W
      device_class: power
      state_class: measurement
write_parameters: []
```

## Bulk register lists

To add many registers, stay in YAML mode and paste a longer `readings:` list directly into the add-on configuration. You do not need a separate registers file.

Example with multiple readings and one writable parameter:

```yaml
readings:
   - name: inverter_temperature
      label: Inverter temperature
      topic_suffix: inverter_temperature
      register_type: input
      address: 5007
      length_words: 1
      data_type: s16
      scale: 0.1
      offset: 0
      decimals: 1
      byte_order: big
      word_order: big
      unit: C
      device_class: temperature
      state_class: measurement
      entityCategory: diagnostic

   - name: mppt1_voltage
      label: MPPT1 voltage
      topic_suffix: mppt1_voltage
      register_type: input
      address: 5010
      length_words: 1
      data_type: u16
      scale: 0.1
      offset: 0
      decimals: 1
      byte_order: big
      word_order: big
      unit: V
      device_class: voltage
      state_class: measurement
      entityCategory: diagnostic

   - name: total_active_power
      label: Total active power
      topic_suffix: total_active_power
      register_type: input
      address: 13033
      length_words: 2
      data_type: s32
      scale: 1.0
      offset: 0
      decimals: 0
      byte_order: big
      word_order: little
      unit: W
      device_class: power
      state_class: measurement
      entityCategory: diagnostic

write_parameters:
   - name: battery_max_soc
      label: Battery max SoC
      register_type: holding
      address: 13057
      length_words: 1
      data_type: u16
      scale: 0.1
      offset: 0
      decimals: 1
      byte_order: big
      word_order: big
      entityCategory: config
      min_value: 50.0
      max_value: 100.0
```

Each reading supports these fields:

- `name`: internal identifier
- `label`: optional friendly name
- `icon`: optional Home Assistant icon
- `topic_suffix`: MQTT topic suffix under your `topic_prefix`
- `register_type`: `input` or `holding`
- `address`: Modbus register address
- `length_words`: `1` for 16-bit values, `2` for 32-bit values
- `data_type`: `u16`, `s16`, `u32`, `s32`, or `f32`
- `scale`, `offset`, `decimals`: conversion and rounding settings
- `byte_order`, `word_order`: endianness controls
- `unit`, `device_class`, `state_class`: optional metadata for Home Assistant
- `entityCategory`: optional `config` or `diagnostic`
- `writable`: optional boolean, normally `false` for readings

Each `write_parameters` entry supports the same Modbus decoding fields plus:

- `min_value`: optional lower bound
- `max_value`: optional upper bound

## Safety for write operations

Write mode is disabled by default for safety:

- `allow_writes: false`

To allow writes:

- `allow_writes: true`

Only parameters listed under `write_parameters` are writable.
