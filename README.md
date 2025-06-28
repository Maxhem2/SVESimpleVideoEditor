# Simple Video Editor (SVE)

A straightforward simple video editor built with Python. SVE provides essential tools for trimming, cropping, and extracting audio from video clips through a clean user interface.

## Features

Video Trimming, Interactive Cropping, Audio Extraction, Mute Audio

## Installation (For Users)

You can download the latest installer for Windows from the **[Releases](https://github.com/Maxhem2/SVESimpleVideoEditor/releases)** page.

1.  Go to the [Releases](https://github.com/Maxhem2/SVESimpleVideoEditor/releases) page.
2.  Find the latest release, which will be at the top.
3.  Under the "Assets" section, download the `SVE-Installer-v...exe` file.
4.  Run the downloaded installer to set up the application on your system.

## Getting Started (For Developers tested on Python 3.12)

### 1\. Set Up the Environment

First, clone this repository and create a virtual environment to manage dependencies.

```bash
# Clone the repository
git clone https://github.com/Maxhem2/SVESimpleVideoEditor.git
cd SVESimpleVideoEditor

# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# On Windows:
.\venv\Scripts\activate

# Make sure your virtual environment is active
pip install -r requirements.txt
```

### 2\. Running the Application

Once the dependencies are installed, you can run the application directly:

```bash
python SimpleVideoEditor.py
```

## License

This project is licensed under the MIT License - see the `LICENSE.md` file for details.