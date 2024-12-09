import streamlit as st
import pandas as pd
import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timedelta
from dateutil.parser import parse as date_parse
from ics import Calendar, Event
import requests

st.title("Dynamic ICS Calendar Sync with GitHub Gist")

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

engine = create_engine("sqlite:///events.db")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
session = Session()

if "github" not in st.secrets:
    st.error("GitHub secrets not found.")
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

def parse_event(row):
    # Parse as naive local times:
    start_str = f"{row['Start Date']} {row['Start Time']}"
    end_str = f"{row['End Date']} {row['End Time']}"
    start_dt = date_parse(start_str) # Naive datetime
    end_dt = date_parse(end_str)     # Naive datetime

    return {
        "subject": row["Subject"],
        "start_datetime": start_dt,
        "end_datetime": end_dt,
        "location": row["Location"],
        "description": row["Description"],
        "unique_key": create_unique_key(row)
    }

def sync_events(df):
    existing_events = session.query(EventRecord).all()
    existing_map = {e.unique_key: e for e in existing_events}

    incoming_data = [parse_event(row) for _, row in df.iterrows()]
    incoming_map = {e['unique_key']: e for e in incoming_data}

    existing_keys = set(existing_map.keys())
    incoming_keys = set(incoming_map.keys())

    to_add = incoming_keys - existing_keys
    to_remove = existing_keys - incoming_keys
    to_potentially_update = existing_keys.intersection(incoming_keys)

    added = []
    updated = []
    deleted = []

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

    for key in to_remove:
        rec = existing_map[key]
        session.delete(rec)
        deleted.append(rec.subject)

    session.commit()
    return added, updated, deleted

def generate_ics():
    cal = Calendar()
    events = session.query(EventRecord).all()

    for ev in events:
        ics_event = Event()
        ics_event.name = ev.subject

        # Provide naive datetimes directly (floating times)
        ics_event.begin = ev.start_datetime
        ics_event.end = ev.end_datetime

        duration = ev.end_datetime - ev.start_datetime
        is_multiple_of_24 = (duration.total_seconds() % 86400 == 0)
        starts_midnight = (ev.start_datetime.hour == 0 and ev.start_datetime.minute == 0 and ev.start_datetime.second == 0)
        ends_midnight = (ev.end_datetime.hour == 0 and ev.end_datetime.minute == 0 and ev.end_datetime.second == 0)
        if is_multiple_of_24 and starts_midnight and ends_midnight:
            ics_event.make_all_day()

        ics_event.location = ev.location
        ics_event.uid = ev.unique_key
        ics_event.created = datetime.now()

        cal.events.add(ics_event)

    ics_str = str(cal)

    vtimezone = """BEGIN:VTIMEZONE
TZID:America/Chicago
X-LIC-LOCATION:America/Chicago
BEGIN:DAYLIGHT
TZOFFSETFROM:-0600
TZOFFSETTO:-0500
TZNAME:CDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0500
TZOFFSETTO:-0600
TZNAME:CST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE
"""

    lines = ics_str.split('\n')
    # Add X-WR-TIMEZONE
    for i, line in enumerate(lines):
        if line.strip() == 'BEGIN:VCALENDAR':
            lines.insert(i+1, 'X-WR-TIMEZONE:America/Chicago')
            break

    # Insert VTIMEZONE before END:VCALENDAR
    for i, line in enumerate(lines):
        if line.strip() == 'END:VCALENDAR':
            lines.insert(i, vtimezone.strip())
            break

    # Add TZID to DTSTART/DTEND lines. They should have no Z since times are naive.
    new_lines = []
    for line in lines:
        if line.startswith("DTSTART:") and 'Z' not in line:
            # Add TZID
            parts = line.split("DTSTART:")
            datetime_str = parts[1]
            new_line = f"DTSTART;TZID=America/Chicago:{datetime_str}"
            new_lines.append(new_line)
        elif line.startswith("DTEND:") and 'Z' not in line:
            parts = line.split("DTEND:")
            datetime_str = parts[1]
            new_line = f"DTEND;TZID=America/Chicago:{datetime_str}"
            new_lines.append(new_line)
        else:
            new_lines.append(line)

    final_ics = "\n".join(new_lines)
    return final_ics

def update_gist_ics(content: str):
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

    parts = raw_url.split('/')
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
                    added, updated, deleted = sync_events(df)
                    ics_content = generate_ics()
                    stable_ics_link = update_gist_ics(ics_content)

                st.success("Events processed successfully!")
                st.write("**Summary of changes:**")
                st.write(f"- Added events: {len(added)}")
                if added:
                    st.write(", ".join(added))
                st.write(f"- Updated events: {len(updated)}")
                if updated:
                    st.write(", ".join(updated))
                st.write(f"- Deleted events: {len(deleted)}")
                if deleted:
                    st.write(", ".join(deleted))

                st.markdown(f"**ICS Link:** [Subscribe to Calendar]({stable_ics_link})")
                st.info("Events should now appear at the correct local times in Apple Calendar.")
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

if st.button("Clear all events"):
    session.query(EventRecord).delete()
    session.commit()
    st.success("All events have been cleared.")
    st.rerun()
