# 🍺 ESP32 Alcohol Detection System

A real-time alcohol detection system built with ESP32 and a React + Vite + Tailwind CSS web dashboard for monitoring and visualization.

---

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Hardware Requirements](#hardware-requirements)
- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [Team](#team)

---

## 📌 Overview

This system uses an ESP32 microcontroller paired with an alcohol/gas sensor (MQ-3) to detect alcohol concentration in the air. Sensor data is transmitted and displayed on a web-based dashboard built with React, Vite, and Tailwind CSS, providing real-time readings and historical data visualization.

---

## ✨ Features

- 🔴 Real-time alcohol level monitoring
- 📊 Dashboard with Chart.js data visualization
- 📱 Responsive multi-page web interface
- ⚠️ Threshold-based alert system
- 🗂️ Historical data logging

---

## 🛠 Tech Stack

**Frontend**
- React 18
- Vite
- Tailwind CSS
- Chart.js

**Hardware**
- ESP32 Microcontroller
- MQ-3 Alcohol Sensor
- Arduino IDE / PlatformIO

---

## 🔧 Hardware Requirements

| Component | Description |
|---|---|
| ESP32 | Main microcontroller |
| MQ-3 Sensor | Alcohol / gas detection |
| Jumper Wires | For connections |
| Breadboard | Circuit prototyping |
| USB Cable | Power & serial communication |

---

## 🚀 Getting Started

### Prerequisites

- Node.js (v18+)
- npm
- Git
- Arduino IDE or PlatformIO (for ESP32 firmware)

### 1. Clone the repository

```bash
git clone git@github.com:yourusername/esp32-alcohol-detection.git
cd esp32-alcohol-detection
```

### 2. Install frontend dependencies

```bash
cd frontend
npm install
```

### 3. Run the development server

```bash
npm run dev
```

Open your browser at `http://localhost:5173`

### 4. Flash the ESP32

- Open the `/firmware` folder in Arduino IDE
- Select the correct board: **ESP32 Dev Module**
- Select the correct COM port
- Click **Upload**

### 5. Environment Variables

Create a `.env` file in the `/frontend` directory:

```env
VITE_API_URL=http://your-esp32-ip-address
```

> ⚠️ Never commit your `.env` file. It is already in `.gitignore`.

---

## 📁 Project Structure

```
esp32-alcohol-detection/
├── frontend/               # React + Vite + Tailwind web app
│   ├── src/
│   │   ├── components/     # Reusable UI components
│   │   ├── pages/          # Dashboard, History, Settings
│   │   └── main.jsx
│   ├── index.html
│   └── vite.config.js
├── firmware/               # ESP32 Arduino code
│   └── main.ino
├── .gitignore
└── README.md
```

---

## 👥 Team

| Name | Role |
|---|---|
| (Your Name) | Full Stack & Hardware |
| (Groupmate 1) | Frontend Developer |
| (Groupmate 2) | Hardware & Firmware |

> 📚 Submitted as a requirement for **Computer Engineering** — *[Your School Name]*

---

## 📄 License

This project is for academic purposes only.
