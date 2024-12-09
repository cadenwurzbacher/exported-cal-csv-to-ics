This Streamlit app lets you upload Outlook calendar events (CSV), sync them to a GitHub Gist as an ICS file, and generate a subscription link for Google Calendar or Apple Calendar.

Features
Upload calendar CSV files.
Sync events to an SQLite database.
Generate an ICS file for the next 3 months.
Update a GitHub Gist with the latest ICS file.
Share a subscription link for calendar apps.
Setup Instructions

1. Clone the Repository
bash
Copy code
git clone https://github.com/your-username/your-repo-name.git
cd your-repo-name

2. Set Up Virtual Environment
bash
Copy code
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
venv\Scripts\activate     # Windows
pip install -r requirements.txt

3. Configure GitHub Gist

Create a Gist:

Go to GitHub Gist.
Create a gist named events.ics with placeholder content:

BEGIN:VCALENDAR
VERSION:2.0
END:VCALENDAR

Copy the Gist ID from the URL (e.g., 1234567890abcdef1234567890abcdef).

Generate GitHub Token:

Create a Personal Access Token with gist scope.
Copy the token.
Add Secrets: Create .streamlit/secrets.toml in the project directory:

[github]
token = "YOUR_GITHUB_TOKEN"
gist_id = "YOUR_GIST_ID"

Run the App

Start the Streamlit app: streamlit run app.py

How to Use

Upload a CSV with columns:
Subject, Start Date, Start Time, End Date, End Time, Location, Description.
Copy the subscription link provided by the app.
Add the link to your calendar app (e.g., Google Calendar).
