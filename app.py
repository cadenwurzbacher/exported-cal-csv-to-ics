import streamlit as st
import pandas as pd
import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, MetaData, Table
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timedelta
from dateutil.parser import parse as date_parse
from ics import Calendar, Event
import requests

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

def parse_event(row):
    start_str = f"{row['Start Date']} {row['Start Time']}"
    end_str = f"{row['End Date']} {row['End Time']}"
    start_dt = date_parse(start_str)
    end_dt = date_parse(end_str)
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

def generate_ics(include_description=True):
    # Generate ICS for the next 3 months
    cal = Calendar()
    now = datetime.utcnow()
    future_limit = now + timedelta(days=90)
    
    events = session.query(EventRecord).filter(
        EventRecord.start_datetime >= now,
        EventRecord.start_datetime <= future_limit
    ).all()
    
    for ev in events:
        ics_event = Event()
        ics_event.name = ev.subject
        ics_event.begin = ev.start_datetime
        ics_event.end = ev.end_datetime
        ics_event.location = ev.location
        
        # Only set description if include_description is True and event has one
        if include_description and ev.description:
            ics_event.description = ev.description
        
        ics_event.uid = ev.unique_key
        # Ensure DTSTAMP is included
        ics_event.created = datetime.utcnow()
        
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
    return raw_url

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
            # Add checkbox to decide whether to include descriptions or not
            include_desc = st.checkbox("Include descriptions from CSV?", value=True)

            if st.button("Process & Update ICS"):
                with st.spinner("Processing events..."):
                    added, updated, deleted = sync_events(df)
                    ics_content = generate_ics(include_description=include_desc)
                    ics_link = update_gist_ics(ics_content)

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

                st.markdown(f"**ICS Link:** [Subscribe to Calendar]({ics_link})")
                st.info("Note: To comply with MIME type requirements (`text/calendar`), serve this ICS file from a location that sets the correct Content-Type header.")
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
    st.rerun()
