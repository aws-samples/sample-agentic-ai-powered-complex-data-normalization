#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Streamlit UI for Criminal Charge Classifier
Shows agent reasoning in real-time
"""

import streamlit as st
import os
import sys
from datetime import datetime

# Add lambda_functions to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lambda_functions'))

from classifier_agent import (HUMAN_REVIEW_NOTICE, classify_charge,
                              create_classification_agent, guardrail_enabled)
from taxonomies import get_profile
from web_search import create_search_client

# This UI's form is built for the law enforcement worked example.
PROFILE = get_profile('law_enforcement')

# Must be first Streamlit command
st.set_page_config(
    page_title="Criminal Charge Classifier",
    page_icon="⚖️",
    layout="wide"
)


def check_password() -> bool:
    """Gate the app behind a shared password.

    The UI processes sensitive criminal charge data, so it MUST NOT be exposed
    unauthenticated. Set APP_PASSWORD in the environment. If it is unset, the app
    refuses to run rather than defaulting to open access.

    For anything beyond a local/demo footprint, front this with Cognito, an
    ALB+OIDC listener, or Streamlit's built-in auth instead of this shared
    password, and never expose the UI to an internet-reachable endpoint.
    """
    expected = os.getenv("APP_PASSWORD")
    if not expected:
        st.error(
            "APP_PASSWORD is not set. Refusing to start without authentication. "
            "Set APP_PASSWORD in the environment (and do not deploy this UI to an "
            "internet-reachable endpoint)."
        )
        st.stop()

    if st.session_state.get("authenticated"):
        return True

    entered = st.text_input("Password", type="password")
    if not entered:
        st.stop()
    # Constant-time compare to avoid timing leaks.
    import hmac
    if hmac.compare_digest(entered, expected):
        st.session_state["authenticated"] = True
        return True
    st.error("Incorrect password.")
    st.stop()


check_password()




@st.cache_resource
def get_cached_agent():
    """Create and cache the Strands agent"""
    return create_classification_agent(create_search_client(), PROFILE)


def main():
    st.title("⚖️ Criminal Charge Classifier")
    st.markdown("**Agentic AI with Web Search** - Powered by Strands and Web Search on Amazon Bedrock AgentCore")
    st.warning(HUMAN_REVIEW_NOTICE)
    if not guardrail_enabled():
        st.error("GUARDRAIL_ID is not set: input screening (prompt-attack blocking and PII "
                 "masking) is off. See the README to enable it.")
    
    st.divider()
    
    # Sidebar for examples
    with st.sidebar:
        st.header("📋 Example Charges")
        
        if st.button("DWI 2nd Offense (TX)"):
            st.session_state.charge = "DWI 2nd Offense"
            st.session_state.state = "TX"
            st.session_state.county = "Travis County"
            st.session_state.code = "49.04"
            st.session_state.offense_type = "Misdemeanor"
        
        if st.button("Drug Possession (TX)"):
            st.session_state.charge = "POSS CS PG 1 <1G"
            st.session_state.state = "TX"
            st.session_state.county = "Harris County"
            st.session_state.code = "481.115"
            st.session_state.offense_type = "Felony"
        
        if st.button("Burglary (TX)"):
            st.session_state.charge = "BURGLARY OF HABITATION"
            st.session_state.state = "TX"
            st.session_state.county = "Dallas County"
            st.session_state.code = "30.02"
            st.session_state.offense_type = "Felony"
        
        st.divider()
        st.markdown("### 💡 Features")
        st.markdown("""
        - ✓ Multi-step reasoning
        - ✓ Web search for statutes
        - ✓ Confidence scoring
        - ✓ Source citations
        """)
    
    # Main form
    col1, col2 = st.columns(2)
    
    with col1:
        charge = st.text_input(
            "Charge Text *",
            value=st.session_state.get('charge', ''),
            placeholder="e.g., DWI 2nd Offense"
        )
        
        state = st.text_input(
            "State",
            value=st.session_state.get('state', ''),
            placeholder="e.g., TX"
        )
        
        county = st.text_input(
            "County",
            value=st.session_state.get('county', ''),
            placeholder="e.g., Travis County"
        )
    
    with col2:
        code = st.text_input(
            "Charge Code",
            value=st.session_state.get('code', ''),
            placeholder="e.g., 49.04"
        )
        
        offense_type = st.selectbox(
            "Offense Type",
            ["", "Felony", "Misdemeanor"],
            index=["", "Felony", "Misdemeanor"].index(st.session_state.get('offense_type', ''))
        )
        
        date = st.date_input("Date of Offense", value=None)
    
    st.divider()
    
    if st.button("🚀 Classify Charge", type="primary", use_container_width=True):
        if not charge:
            st.error("Please enter a charge text")
            return
        
        # Build query
        query_parts = [f"Classify this criminal charge: \"{charge}\""]
        if state:
            query_parts.append(f"State: {state}")
        if county:
            query_parts.append(f"County: {county}")
        if code:
            query_parts.append(f"Charge Code: {code}")
        if offense_type:
            query_parts.append(f"Offense Type: {offense_type}")
        if date:
            query_parts.append(f"Date of Offense: {date.isoformat()}")
        
        query = "\n".join(query_parts)
        
        # Show input
        with st.expander("📝 Input to Agent", expanded=False):
            st.code(query, language="text")
        
        st.divider()
        
        # Agent reasoning section
        st.subheader("🤖 Agent Reasoning Process")
        
        reasoning_container = st.container()
        
        with reasoning_container:
            with st.status("Agent is analyzing...", expanded=True) as status:
                st.write("Step 1: Analyzing charge context...")
                
                # Create agent
                agent = get_cached_agent()
                
                st.write("Step 2: Agent deciding on tool use...")
                
                # Build charge data
                charge_data = {
                    'charge_text': charge,
                    'state': state,
                    'county': county,
                    'charge_code': code,
                    'offense_type': offense_type,
                    'date_of_offense': date.isoformat() if date else None
                }
                
                # Classify using shared logic
                try:
                    st.write("Step 3: Processing web search results...")
                    st.write("Step 4: Generating classification...")
                    
                    result = classify_charge(agent, charge_data)
                    
                    status.update(label="✅ Classification Complete!", state="complete", expanded=False)
                    
                except Exception as e:
                    st.error(f"Error: {str(e)}")
                    status.update(label="❌ Classification Failed", state="error")
                    return
        
        st.divider()
        
        # Results
        st.subheader("📊 Classification Result")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.metric("NIBRS Code", result.get('code', 'N/A'))
        
        with col2:
            confidence = result.get('confidence', 0)
            st.metric("Confidence", f"{confidence*100:.0f}%")
        
        with col3:
            web_grounded = result.get('web_grounded', False)
            st.metric("Web Grounded", "Yes" if web_grounded else "No")
        
        st.markdown(f"**Offense Name:** {result.get('description', 'N/A')}")
        
        if result.get('statute_reference'):
            st.markdown(f"**Statute:** {result['statute_reference']}")
        
        st.markdown("**Reasoning:**")
        st.info(result.get('reasoning', 'No reasoning provided'))
        
        if result.get('needs_review'):
            st.warning("Flagged for human review (low confidence or fallback code).")

        if result.get('sources'):
            st.markdown("**Sources** (verified against search results):")
            for source in result['sources']:
                st.markdown(f"- [{source.get('title') or source['url']}]({source['url']})")
        
        # Save to session
        if 'history' not in st.session_state:
            st.session_state.history = []
        
        st.session_state.history.append({
            'timestamp': datetime.now().isoformat(),
            'input': {'charge': charge, 'state': state, 'county': county, 'code': code},
            'result': result
        })
    
    # Show history
    if 'history' in st.session_state and st.session_state.history:
        st.divider()
        st.subheader("📜 Classification History")
        
        for i, item in enumerate(reversed(st.session_state.history[-5:]), 1):
            with st.expander(f"{i}. {item['input']['charge']} → {item['result'].get('code', 'N/A')}"):
                st.json(item['result'])


if __name__ == '__main__':
    main()
