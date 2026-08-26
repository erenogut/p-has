# P-HAS (Production Human Analytics System)

## About the Project
P-HAS is an AI-powered desktop application designed for real-time personnel tracking over IP cameras in production facilities and factories. It analyzes movement within custom-drawn zones, verifies if personnel are in their assigned areas according to their shift schedules, and logs detailed reports to a database.

The system is developed to automate occupational safety and efficiency processes, featuring a user-friendly interface and a high-performance artificial intelligence infrastructure.

## Technologies Used
* **Python 3:** Core programming language.
* **Ultralytics YOLO:** Real-time human and object detection. The project supports both TensorRT (.engine) for hardware acceleration and standard PyTorch (.pt) models.
* **OpenCV & NumPy:** Image processing, RTSP stream capture, frame drawing, and matrix computations.
* **PyQt6:** Modern, dark-themed, and user-friendly desktop Graphical User Interface (GUI).
* **SciPy (Hungarian Algorithm) & Numba:** Advanced object tracking, ID assignment, and performance optimizations.
* **PyODBC (SQL Server):** Logging obtained data into the corporate database and generating reports.

## Key Features
* **Real-Time RTSP Analysis:** Capturing and analyzing live video feeds from network IP cameras with minimal latency.
* **Customizable Zone Drawing:** Drawing and naming multiple polygonal zones over the camera view directly through the GUI.
* **Shift and Personnel Management:** Defining which personnel should be working at what time, on which camera, and in which specific zone.
* **AI Model Interface (Fine-Tuning):** Automatically filtering false or difficult detections (Hard Frames) and allowing direct retraining of the model via the system interface.
* **Video Recording and Logging:** Saving analyzed camera feeds (including bounding boxes and IDs) as local video files.

## Reporting and Database Outputs
The system records data to a SQL Server based on predefined rules. The advanced reporting module provides:
* Entry and exit times of personnel for specific cameras or zones.
* Total active time spent by personnel in their assigned work zones.
* Violation events (e.g., personnel detected outside their shift hours, found in unauthorized zones, or leaving their assigned zone for extended periods).
* Retrospective movement and performance summaries based on date or camera.

## Installation and Execution

1. Install the required libraries:
```bash
pip install -r requirements.txt
```

2. Add your AI model to the project:
Place your trained YOLO model in the root directory of the project, naming it either `best.pt` or `best.engine`.

3. Setup the configuration file:
If this is your first time setting up the project, create a copy of `config.example.json` and rename it to `config.json`. Update it with your IP camera (RTSP) credentials and SQL database information.

4. Run the application:
```bash
python app.py
```
