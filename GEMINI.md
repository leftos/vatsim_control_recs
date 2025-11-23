# GEMINI.md

## Project Overview

This project is a Python application that provides a terminal-based user interface for analyzing VATSIM flight data and controller staffing. It uses the `textual` library to create the UI, `requests` to fetch data from the VATSIM API, and `spacy` for natural language processing to disambiguate airport names.

The application displays information about airports, including departing and arriving flights, estimated time of arrival (ETA) for the next arrival, and currently staffed controller positions. It also supports custom groupings of airports.

## Building and Running

### Prerequisites

*   Python 3
*   pip

### Installation

1.  Clone the repository.
2.  Install the required Python packages:

    ```bash
    pip install -r requirements.txt
    ```
3. A `spacy` language model is required for the airport name disambiguation. Download the `en_core_web_sm` model:
    ```bash
    python -m spacy download en_core_web_sm
    ```

### Running the Application

Run the `main.py` script to start the application:

```bash
python main.py
```

You can also use various command-line arguments to customize the application's behavior. For example, to track specific airports:

```bash
python main.py --airports KSFO KLAX
```

For a full list of options, run:

```bash
python main.py --help
```

## Development Conventions

*   **UI Framework:** The application uses the `textual` framework for its terminal-based UI. UI components are defined in the `ui/` directory.
*   **Backend Logic:** The core data analysis logic is located in the `backend/` directory.
*   **Data:** The `data/` directory contains various data files used by the application, such as airport information and aircraft data.
*   **Modularity:** The code is organized into modules with specific responsibilities (e.g., `data`, `core`, `ui`).
*   **Type Hinting:** The code uses Python's type hinting to improve code clarity and maintainability.
*   **Linting:** The project uses `flake8` for linting.
*   **Testing:** The project does not currently have a dedicated test suite.
