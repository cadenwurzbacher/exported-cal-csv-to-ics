import streamlit as st
import pandas as pd
import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timedelta
from dateutil.parser import parse as date_parse
from ics import Calendar, Event
import requests
from zoneinfo import ZoneInfo

st.title("Dynamic ICS Calendar Sync with GitHub Gist")

# --- Declarative Base Setup ---
Base = declarative_base()

class EventRecord(Base):
    __tablename__ = 'events'
    id = Column(Integer, primary_key=True)
    subject = Column(String)
    start_datetime = Column(DateTime)
    end_datetime = Column(DateTime)
    location = Column(String)
    description = Column(String)
    unique_key = Column(String, unique=True)

# --- Database Setup ---
engine = create_engine("sqlite:///events.db")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
session = Session()

# --- Check for GitHub Secrets ---
if "github" not in st.secrets:
    st.error("GitHub secrets not found in `st.secrets`. Add them in Streamlit secrets.")
    st.stop()

GITHUB_TOKEN = st.secrets["github"]["token"]
GIST_ID = st.secrets["github"]["gist_id"]

def validate_csv(df: pd.DataFrame):
    required_columns = ["Subject", "Start Date", "Start Time", "End Date", "End Time", "Location", "Description"]
    for col in required_columns:
        if col not in df.columns:
            return False, f"Missing required column: {col}"
    return True, ""

def create_unique_key(row):
    return f"{row['Subject']}|{row['Start Date']}|{row['Start Time']}"

def parse_event(row, tz: ZoneInfo):
    start_str = f"{row['Start Date']} {row['Start Time']}"
    end_str = f"{row['End Date']} {row['End Time']}"
    start_dt = date_parse(start_str)
    end_dt = date_parse(end_str)

    # Localize to the selected timezone
    start_dt = start_dt.replace(tzinfo=tz)
    end_dt = end_dt.replace(tzinfo=tz)

    return {
        "subject": row["Subject"],
        "start_datetime": start_dt,
        "end_datetime": end_dt,
        "location": row["Location"],
        "description": row["Description"],
        "unique_key": create_unique_key(row)
    }

def sync_events(df, tz: ZoneInfo):
    existing_events = session.query(EventRecord).all()
    existing_map = {e.unique_key: e for e in existing_events}
    
    incoming_data = [parse_event(row, tz) for _, row in df.iterrows()]
    incoming_map = {e['unique_key']: e for e in incoming_data}
    
    existing_keys = set(existing_map.keys())
    incoming_keys = set(incoming_map.keys())
    
    to_add = incoming_keys - existing_keys
    to_remove = existing_keys - incoming_keys
    to_potentially_update = existing_keys.intersection(incoming_keys)
    
    added = []
    updated = []
    deleted = []
    
    # Add new events
    for key in to_add:
        data = incoming_map[key]
        new_record = EventRecord(
            subject=data['subject'],
            start_datetime=data['start_datetime'],
            end_datetime=data['end_datetime'],
            location=data['location'],
            description=data['description'],
            unique_key=data['unique_key']
        )
        session.add(new_record)
        added.append(data['subject'])
    
    # Update existing events if changed
    for key in to_potentially_update:
        incoming = incoming_map[key]
        existing = existing_map[key]
        changed = False
        if (existing.subject != incoming['subject'] or
            existing.start_datetime != incoming['start_datetime'] or
            existing.end_datetime != incoming['end_datetime'] or
            existing.location != incoming['location'] or
            existing.description != incoming['description']):
            existing.subject = incoming['subject']
            existing.start_datetime = incoming['start_datetime']
            existing.end_datetime = incoming['end_datetime']
            existing.location = incoming['location']
            existing.description = incoming['description']
            changed = True
        if changed:
            updated.append(incoming['subject'])
    
    # Remove events not in incoming file
    for key in to_remove:
        rec = existing_map[key]
        session.delete(rec)
        deleted.append(rec.subject)
    
    session.commit()
    return added, updated, deleted

def generate_ics(tz: ZoneInfo):
    # Generate ICS for ALL events (no time limit)
    cal = Calendar()
    events = session.query(EventRecord).all()
    
    for ev in events:
        ics_event = Event()
        ics_event.name = ev.subject

        start_dt = ev.start_datetime.astimezone(tz)
        end_dt = ev.end_datetime.astimezone(tz)

        duration = end_dt - start_dt

        # Check if duration is a multiple of 24 hours and starts/ends at midnight
        is_multiple_of_24 = (duration.total_seconds() % 86400 == 0)
        starts_at_midnight = (start_dt.hour == 0 and start_dt.minute == 0 and start_dt.second == 0)
        ends_at_midnight = (end_dt.hour == 0 and end_dt.minute == 0 and end_dt.second == 0)

        if is_multiple_of_24 and starts_at_midnight and ends_at_midnight:
            # All-day (or multi-day all-day) event
            ics_event.begin = start_dt.date()
            ics_event.end = end_dt.date()
            ics_event.make_all_day()
        else:
            ics_event.begin = start_dt
            ics_event.end = end_dt

        ics_event.location = ev.location
        # Do not include description
        # ics_event.description = ev.description
        ics_event.uid = ev.unique_key
        # Ensure DTSTAMP is included
        ics_event.created = datetime.now(tz)
        
        cal.events.add(ics_event)
    
    return str(cal)

def update_gist_ics(content: str):
    # Update the gist file with the new ICS content
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "files": {
            "events.ics": {
                "content": content
            }
        }
    }
    response = requests.patch(url, headers=headers, json=data)
    response.raise_for_status()
    gist_data = response.json()
    raw_url = gist_data["files"]["events.ics"]["raw_url"]

    # raw_url typically looks like:
    # https://gist.githubusercontent.com/<username>/<gist_id>/raw/<revision_hash>/events.ics
    # We can remove the revision_hash to get a stable link:
    parts = raw_url.split('/')
    # parts = ["https:", "", "gist.githubusercontent.com", "<username>", "<gist_id>", "raw", "<revision_hash>", "events.ics"]
    username = parts[3]
    gist_id = parts[4]

    stable_raw_url = f"https://gist.githubusercontent.com/{username}/{gist_id}/raw/events.ics"
    return stable_raw_url

def search_events(query: str):
    q = f"%{query}%"
    results = session.query(EventRecord).filter(
        (EventRecord.subject.like(q)) | (EventRecord.description.like(q))
    ).all()
    return results

# Timezone selection (default to Central Time - America/Chicago)
available_tzs = ["America/Chicago", "America/New_York", "America/Los_Angeles", "UTC"]
selected_tz = st.selectbox("Select the timezone for event times:", available_tzs, index=0)
tz = ZoneInfo(selected_tz)

uploaded_file = st.file_uploader("Upload Outlook CSV", type=["csv"])
if uploaded_file is not None:
    try:
        df = pd.read_csv(uploaded_file)
        valid, msg = validate_csv(df)
        if not valid:
            st.error(msg)
        else:
            if st.button("Process & Update ICS"):
                with st.spinner("Processing events..."):
                    added, updated, deleted = sync_events(df, tz)
                    ics_content = generate_ics(tz)
                    stable_ics_link = update_gist_ics(ics_content)

                st.success("Events processed successfully!")
                st.write("**Summary of changes:**")
                st.write(f"- Added events: {len(added)}")
                if len(added) > 0:
                    st.write(", ".join(added))
                st.write(f"- Updated events: {len(updated)}")
                if len(updated) > 0:
                    st.write(", ".join(updated))
                st.write(f"- Deleted events: {len(deleted)}")
                if len(deleted) > 0:
                    st.write(", ".join(deleted))

                st.markdown(f"**ICS Link:** [Subscribe to Calendar]({stable_ics_link})")
                st.info("This link will remain stable and always point to the latest version of your ICS file. However, to comply with MIME type requirements (`text/calendar`), ensure it's served from a location that sets the correct Content-Type header.")
    except Exception as e:
        st.error(f"Error processing file: {e}")

st.markdown("---")
st.subheader("View/Search Events")

search_term = st.text_input("Search by subject or description")
if search_term:
    results = search_events(search_term)
else:
    results = session.query(EventRecord).all()

if results:
    data = [{
        "Subject": r.subject,
        "Start": r.start_datetime,
        "End": r.end_datetime,
        "Location": r.location,
        "Description": r.description
    } for r in results]
    st.dataframe(pd.DataFrame(data))
else:
    st.write("No events found.")

# Add a button to clear all events
if st.button("Clear all events"):
    session.query(EventRecord).delete()
    session.commit()
    st.success("All events have been cleared.")
    st.experimental_rerun()
