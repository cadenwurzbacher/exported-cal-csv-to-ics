import streamlit as st
import pandas as pd
import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timedelta
from dateutil.parser import parse as date_parse
from ics import Calendar, Event
import requests

st.title("Floating-Time ICS Calendar Sync")

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
    # Parse as naive datetimes (no timezone):
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

def generate_ics_free_all_day():
    """Generate ICS with all-day events marked as free (transparent)"""
    cal = Calendar()
    events = session.query(EventRecord).all()
    
    debug_info = []

    for ev in events:
        ics_event = Event()
        ics_event.name = ev.subject

        # Provide naive times directly, so they remain floating
        ics_event.begin = ev.start_datetime
        ics_event.end = ev.end_datetime

        # Check duration
        duration = ev.end_datetime - ev.start_datetime
        duration_hours = duration.total_seconds() / 3600
        
        # Mark as free if it's 24 hours or longer (with 1 hour tolerance)
        is_24_hour_event = duration_hours >= 23  # 23+ hours to account for slight variations
        
        if is_24_hour_event:
            ics_event.make_all_day()
            ics_event.transp = "TRANSPARENT"  # Mark as free/busy-free
            debug_info.append(f"✅ FREE (24h+): {ev.subject} (Start: {ev.start_datetime}, End: {ev.end_datetime}, Duration: {duration_hours:.1f}h)")
        else:
            debug_info.append(f"❌ BUSY: {ev.subject} (Start: {ev.start_datetime}, End: {ev.end_datetime}, Duration: {duration_hours:.1f}h)")

        ics_event.location = ev.location
        ics_event.uid = ev.unique_key
        ics_event.created = datetime.now()

        cal.events.add(ics_event)

    # Convert to string and clean up timezone info
    ics_str = str(cal)
    new_lines = []
    for line in ics_str.splitlines():
        if (line.startswith("DTSTART:") or line.startswith("DTEND:")) and line.endswith("Z"):
            new_lines.append(line[:-1])  # Remove the trailing 'Z'
        else:
            new_lines.append(line)

    final_ics = "\n".join(new_lines)
    return final_ics, debug_info

def generate_ics_busy_all_day():
    """Generate ICS with all-day events marked as busy (opaque)"""
    cal = Calendar()
    events = session.query(EventRecord).all()
    
    debug_info = []

    for ev in events:
        ics_event = Event()
        ics_event.name = ev.subject

        # Provide naive times directly, so they remain floating
        ics_event.begin = ev.start_datetime
        ics_event.end = ev.end_datetime

        # Check duration
        duration = ev.end_datetime - ev.start_datetime
        duration_hours = duration.total_seconds() / 3600
        
        # Mark as busy if it's 24 hours or longer (with 1 hour tolerance)
        is_24_hour_event = duration_hours >= 23  # 23+ hours to account for slight variations
        
        if is_24_hour_event:
            ics_event.make_all_day()
            ics_event.transp = "OPAQUE"  # Mark as busy
            debug_info.append(f"🔴 BUSY (24h+): {ev.subject} (Start: {ev.start_datetime}, End: {ev.end_datetime}, Duration: {duration_hours:.1f}h)")
        else:
            debug_info.append(f"❌ BUSY: {ev.subject} (Start: {ev.start_datetime}, End: {ev.end_datetime}, Duration: {duration_hours:.1f}h)")

        ics_event.location = ev.location
        ics_event.uid = ev.unique_key
        ics_event.created = datetime.now()

        cal.events.add(ics_event)

    # Convert to string and clean up timezone info
    ics_str = str(cal)
    new_lines = []
    for line in ics_str.splitlines():
        if (line.startswith("DTSTART:") or line.startswith("DTEND:")) and line.endswith("Z"):
            new_lines.append(line[:-1])  # Remove the trailing 'Z'
        else:
            new_lines.append(line)

    final_ics = "\n".join(new_lines)
    return final_ics, debug_info

def update_gist_ics_free(content: str):
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "files": {
            "events-free.ics": {
                "content": content
            }
        }
    }
    response = requests.patch(url, headers=headers, json=data)
    response.raise_for_status()
    gist_data = response.json()
    raw_url = gist_data["files"]["events-free.ics"]["raw_url"]

    parts = raw_url.split('/')
    username = parts[3]
    gist_id = parts[4]
    stable_raw_url = f"https://gist.githubusercontent.com/{username}/{gist_id}/raw/events-free.ics"
    return stable_raw_url

def update_gist_ics_busy(content: str):
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "files": {
            "events-busy.ics": {
                "content": content
            }
        }
    }
    response = requests.patch(url, headers=headers, json=data)
    response.raise_for_status()
    gist_data = response.json()
    raw_url = gist_data["files"]["events-busy.ics"]["raw_url"]

    parts = raw_url.split('/')
    username = parts[3]
    gist_id = parts[4]
    stable_raw_url = f"https://gist.githubusercontent.com/{username}/{gist_id}/raw/events-busy.ics"
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
                    
                    # Generate both versions
                    ics_content_free, debug_info_free = generate_ics_free_all_day()
                    ics_content_busy, debug_info_busy = generate_ics_busy_all_day()
                    
                    stable_ics_link_free = update_gist_ics_free(ics_content_free)
                    stable_ics_link_busy = update_gist_ics_busy(ics_content_busy)

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

                st.markdown("**ICS Links:**")
                st.markdown(f"🟢 **Free Version** (24h+ events marked as free): [Subscribe]({stable_ics_link_free})")
                st.markdown(f"🔴 **Busy Version** (24h+ events marked as busy): [Subscribe]({stable_ics_link_busy})")
                st.info("Times are floating (no timezone). Apple Calendar should display them at the exact times you provided, based on your device's local time.")
                
                # Show debug info for free version
                st.subheader("Event Classification (Free Version)")
                for info in debug_info_free:
                    st.text(info)
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

st.markdown("---")
st.subheader("Regenerate ICS File")

if st.button("Regenerate ICS with Current Events"):
    if session.query(EventRecord).count() > 0:
        with st.spinner("Regenerating ICS files..."):
            ics_content_free, debug_info_free = generate_ics_free_all_day()
            ics_content_busy, debug_info_busy = generate_ics_busy_all_day()
            
            stable_ics_link_free = update_gist_ics_free(ics_content_free)
            stable_ics_link_busy = update_gist_ics_busy(ics_content_busy)
        
        st.success("ICS files regenerated successfully!")
        st.markdown("**ICS Links:**")
        st.markdown(f"🟢 **Free Version** (24h+ events marked as free): [Subscribe]({stable_ics_link_free})")
        st.markdown(f"🔴 **Busy Version** (24h+ events marked as busy): [Subscribe]({stable_ics_link_busy})")
        
        # Show debug info for free version
        st.subheader("Event Classification (Free Version)")
        for info in debug_info_free:
            st.text(info)
    else:
        st.warning("No events in database to regenerate.")
