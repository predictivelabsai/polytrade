# Mobile API & Thin Client Architecture

This guide explains how to run PolyCode as a distributed system with a generic FastAPI backend and a lightweight Android/Termux client.

## Architecture

*   **Backend (`api/`)**: A FastAPI application running on your server (Ubuntu). It exposes core logic (Prediction, Weather) via REST endpoints.
*   **Client (`mobile/`)**: A lightweight Textual TUI app running on your mobile device (via Termux). It sends commands to the backend and displays JSON results.

## 1. Backend Setup (Server)

1.  Navigate to the project root:
    ```bash
    cd /path/to/polycode
    ```

2.  Install API dependencies:
    ```bash
    pip install -r api/requirements.txt
    ```

3.  Run the API server:
    ```bash
    # Run on all interfaces (0.0.0.0) to allow mobile connection
    # Ensure port 8000 is open in your firewall/security group
    python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
    ```

## 2. Mobile Client Setup (Android/Termux)

1.  **Install Termux** from F-Droid or Google Play.

2.  **Install Python and dependencies**:
    ```bash
    pkg install python
    pip install textual httpx
    ```

3.  **Deploy the Client**:
    *   Copy `mobile/thin_client.py` to your phone (e.g., via `scp`, `git`, or copy-paste).

4.  **Configure Connection**:
    *   Edit `thin_client.py` on your phone.
    *   Change `API_URL` to your server's IP address (e.g., `http://192.168.1.100:8000` or your VPS Public IP).

5.  **Run the Client**:
    ```bash
    python thin_client.py
    ```

## 3. (Optional) Voice Input Setup

Enhance the client with speech-to-text using Android's native speech recognizer.

1.  **Install Termux:API**:
    *   Download and install the **Termux:API** app from F-Droid or Google Play.
    *   In Termux, install the API package:
        ```bash
        pkg install termux-api
        ```
    *   **Grant Permissions**: Go to Android Settings -> Apps -> Termux:API -> Permissions and allow **Microphone** access. Also ensure the main Termux app has Microphone permission.

2.  **Test It**:
    ```bash
    termux-speech-to-text
    ```
    Speak into the phone. The text should appear in the terminal.

3.  **Use It**:
    *   The `mobile/thin_client.py` includes a **🎤 Voice** button.
    *   Press the button, speak your command (e.g., "London"), and the app will populate the input field with the recognized text.

## Usage

*   **Get Weather**: Enter a city and click "Get Weather".
*   **Predict Market**: Enter a city and click "Predict Market". The server will perform the analysis (this may take 10-20 seconds) and return the JSON report.

## Security Note

*   The current implementation uses HTTP. For production execution over the public internet, **setup HTTPS** (e.g., using Nginx + Certbot as a reverse proxy) and consider adding API Key authentication in `api/main.py`.
