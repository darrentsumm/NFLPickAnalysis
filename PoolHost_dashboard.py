import streamlit as st
import pandas as pd
import os
import math
import altair as alt
from dotenv import load_dotenv
from supabase import create_client, Client

# --- Setup and Configuration ---
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

st.set_page_config(page_title="NFL Spread Dashboard", layout="wide")

# --- Initialize Supabase ---
@st.cache_resource
def init_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        st.error("Supabase URL and Key must be set in the .env file.")
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        st.error(f"Failed to initialize Supabase client: {e}")
        return None

supabase_client = init_supabase_client()


# --- Get base info from Supabase ---
#Seasons
def get_available_seasons():
    if supabase_client is None: return []
    try:
        response = supabase_client.table('game').select('season').execute()
        df = pd.DataFrame(response.data)
        if not df.empty:
            return sorted(df['season'].unique().tolist(), reverse=True)
        return [2024]
    except Exception as e:
        st.error(f"Error fetching seasons: {e}")
        return []
# Find Spread Range
def get_spread_range_from_data(df):
    if df.empty: return -10.0, 10.0
    min_s = df['home_spread'].min()
    max_s = df['home_spread'].max()
    min_clean = math.floor(min_s * 2) / 2
    max_clean = math.ceil(max_s * 2) / 2
    return min_clean, max_clean
# Select specific games based on user input
@st.cache_data(show_spinner=False)
def get_raw_spread_data(selected_seasons):
    if supabase_client is None or not selected_seasons: return pd.DataFrame()
    try:
        response = supabase_client.rpc('get_spread_stats', {'seasons': selected_seasons}).execute()
        df = pd.DataFrame(response.data)
        if not df.empty:
            df['home_spread'] = pd.to_numeric(df['home_spread'], errors='coerce')
        return df
    except Exception as e:
        st.error(f"Error fetching spread stats: {e}")
        return pd.DataFrame()
# rebin df based on user input
def rebin_data(df, bin_size):
    if df.empty: return df
    df['bin_floor'] = ((df['home_spread'].round(1) // bin_size) * bin_size).round(1)
    
    grouped = df.groupby('bin_floor').agg({
        'total_games': 'sum', 'total_covers': 'sum',
        'total_home_picks': 'sum', 'total_picks_made': 'sum',
        'home_spread': 'min'
    }).reset_index()
    
    if bin_size == 0.5:
        grouped['spread_bin'] = grouped['bin_floor'].apply(lambda x: f"{x:+.1f}")
    else:
        grouped['spread_bin'] = grouped.apply(lambda x: f"{x['bin_floor']:+.1f} to {x['bin_floor'] + bin_size - 0.1:+.1f}", axis=1)

    grouped['pct_picks_home'] = (grouped['total_home_picks'] / grouped['total_picks_made'].replace(0, 1)) * 100
    grouped['pct_games_home_covered'] = (grouped['total_covers'] / grouped['total_games'].replace(0, 1)) * 100
    return grouped
# Fetch users
def get_unique_users():
    if supabase_client is None: return [" Median Picker"]
    try:
        response = supabase_client.table('User').select('username').execute()
        df = pd.DataFrame(response.data)
        users = sorted(df['username'].unique().tolist()) if not df.empty else []
        users.insert(0, " Median Picker")
        return users
    except Exception as e:
        st.error(f"Error fetching users: {e}")
        return [" Median Picker"]
# Fetch MNF total picks
@st.cache_data(show_spinner=False)
def get_mnf_pool_data(selected_seasons):
    if supabase_client is None: return pd.DataFrame()
    try:
        response = supabase_client.rpc('get_mnf_medians', {'seasons': selected_seasons}).execute()
        df = pd.DataFrame(response.data)
        if not df.empty:
            df['pool_median_total'] = pd.to_numeric(df['pool_median_total'], errors='coerce')
        return df
    except Exception as e:
        st.error(f"Error calculating MNF medians: {e}")
        return pd.DataFrame()
# Get data from ALL games to calculate averages
@st.cache_data(show_spinner=False)
def get_global_game_stats(selected_seasons):
    if supabase_client is None: return pd.DataFrame()
    try:
        response = supabase_client.rpc('get_global_game_stats', {'seasons': selected_seasons}).execute()
        df = pd.DataFrame(response.data)
        if not df.empty:
            df['home_margin'] = pd.to_numeric(df['home_margin'], errors='coerce')
            df['home_pick_pct'] = pd.to_numeric(df['home_pick_pct'], errors='coerce')
        return df
    except Exception as e:
        st.error(f"Error fetching global game stats: {e}")
        return pd.DataFrame()
# Get data from ALL games to calculate averages
@st.cache_data(show_spinner=False)
def get_global_herd_distribution(selected_seasons):
    if supabase_client is None: return {}
    try:
        games_res = supabase_client.table('game').select('game_id').in_('season', selected_seasons).execute()
        if not games_res.data: return {}
        all_game_ids = [g['game_id'] for g in games_res.data]

        batch_size = 1000
        all_consensus = []
        for i in range(0, len(all_game_ids), batch_size):
            batch_ids = all_game_ids[i:i + batch_size]
            try:
                res = supabase_client.rpc('get_game_consensus', {'target_game_ids': batch_ids}).execute()
                all_consensus.extend(res.data)
            except: pass
        
        if not all_consensus: return {}
        
        total_pool_picks = 0
        counts = {'Herd (Chalk)': 0, 'Contrarian (Lone Wolf)': 0, 'Neutral': 0}
        
        for row in all_consensus:
            n_picks = row['total_picks']
            p_home = row['home_pick_pct']
            p_away = 100.0 - p_home
            
            if p_home > 60: counts['Herd (Chalk)'] += (n_picks * (p_home/100))
            elif p_home < 40: counts['Contrarian (Lone Wolf)'] += (n_picks * (p_home/100))
            else: counts['Neutral'] += (n_picks * (p_home/100))
            
            if p_away > 60: counts['Herd (Chalk)'] += (n_picks * (p_away/100))
            elif p_away < 40: counts['Contrarian (Lone Wolf)'] += (n_picks * (p_away/100))
            else: counts['Neutral'] += (n_picks * (p_away/100))
            
            total_pool_picks += n_picks

        if total_pool_picks > 0:
            return {k: v / total_pool_picks for k, v in counts.items()}
        return {}

    except Exception as e:
        st.error(f"Error calculating global herd stats: {e}")
        return {}
# Get data for specific picker based on user input
@st.cache_data(show_spinner=False)
def get_user_performance_data(user_id, selected_seasons):
    if supabase_client is None: return pd.DataFrame()
    try:
        games_res = supabase_client.table('game') \
            .select('game_id, home_team_id, away_team_id, home_cover, tie_spread, home_score, away_score, home_spread, mnf, week, season') \
            .in_('season', selected_seasons) \
            .eq('tie_spread', False) \
            .execute()
        
        if not games_res.data: return pd.DataFrame()
        df_games = pd.DataFrame(games_res.data)
        df_games['home_spread'] = pd.to_numeric(df_games['home_spread'], errors='coerce')
        df_games['actual_total'] = df_games['home_score'] + df_games['away_score']

        df_pool_medians = get_mnf_pool_data(selected_seasons)
        target_game_ids = df_games['game_id'].tolist()

        if user_id == " Median Picker":
            try:
                consensus_res = supabase_client.rpc('get_game_consensus', {'target_game_ids': target_game_ids}).execute()
                df_consensus = pd.DataFrame(consensus_res.data)
            except:
                df_consensus = pd.DataFrame()

            if not df_consensus.empty:
                df_picks = pd.merge(df_games[['game_id']], df_consensus[['game_id', 'home_pick_pct']], on='game_id', how='left')
                df_picks['home_pick_pct'] = df_picks['home_pick_pct'].fillna(50.0)
                df_picks['pick_home'] = df_picks['home_pick_pct'] > 50.0
                df_picks['pick_made'] = True
                df_picks['username'] = " Median Picker"
                
                if not df_pool_medians.empty:
                    df_picks = pd.merge(df_picks, df_pool_medians, on='game_id', how='left')
                    df_picks.rename(columns={'pool_median_total': 'tot_if_picked'}, inplace=True)
                else:
                    df_picks['tot_if_picked'] = None
                
                df_picks = df_picks[['game_id', 'pick_home', 'pick_made', 'tot_if_picked', 'username']]
            else:
                return pd.DataFrame()
        else:
            picks_res = supabase_client.table('pick') \
                .select('game_id, pick_home, pick_made, tot_if_picked') \
                .eq('username', user_id) \
                .in_('game_id', target_game_ids) \
                .eq('pick_made', True) \
                .eq('pick_overwritten', False) \
                .execute()
            
            if not picks_res.data: return pd.DataFrame()
            df_picks = pd.DataFrame(picks_res.data)

            try:
                consensus_res = supabase_client.rpc('get_game_consensus', {'target_game_ids': target_game_ids}).execute()
                df_consensus = pd.DataFrame(consensus_res.data)
            except:
                df_consensus = pd.DataFrame()

        merged = pd.merge(df_games, df_picks, on='game_id', how='inner')
        
        if user_id != " Median Picker" and not df_pool_medians.empty:
             merged = pd.merge(merged, df_pool_medians, on='game_id', how='left')
        elif 'pool_median_total' not in merged.columns and 'tot_if_picked' in merged.columns:
            merged['pool_median_total'] = merged['tot_if_picked']

        if 'home_pick_pct' not in merged.columns and not df_consensus.empty:
            merged = pd.merge(merged, df_consensus[['game_id', 'home_pick_pct']], on='game_id', how='left')
        elif 'home_pick_pct' not in merged.columns:
            merged['home_pick_pct'] = 50.0 
            
        return merged

    except Exception as e:
        st.error(f"Error calculating user stats: {e}")
        return pd.DataFrame()

# --- MAIN APP ---

if supabase_client:
    st.title("🏈 NFL Pick 'Em Analysis")
    st.caption("Explore the trends and outliers of a picks pool I participate in. ")
    with st.expander("Information about this dashboard and it's data"):
            st.markdown("""
            Members of this pool pick every non-Thursday Night Game against a spread that is set Tuesday morning of the week. They also choose a "total points scored" for the last game of each week, commonly the Monday Night game. 

            Since users pick against the spread, the likelyhood of a correct pick is theoretically about 50%. This likelyhood is slightly increased due to the knowledge of injuries, benchings, etc. that may happen between when the spread is set Tuesday morning, and when picks are finalized Sunday at 1 pm.

            Data used includes: Weeks 1-18 2025, Weeks 1-15 2024, Weeks 1-9 2015, and Weeks 1-10 2014.  
            This dashboard only uses games that did not result in a tie against the spread, and excludes picks that were not made/defaulted to home.
            """)

    with st.sidebar:
        st.header("Global Filters")
        available_seasons = get_available_seasons()
        if available_seasons:
            default_season = available_seasons
            selected_seasons = st.multiselect("Select Season(s)", options=available_seasons, default=default_season)
        else:
            selected_seasons = []
            st.warning("No seasons found.")
    
    if not selected_seasons:
        st.stop()

    df_raw = get_raw_spread_data(selected_seasons)
    min_val, max_val = -10.0, 10.0 
    if not df_raw.empty:
        min_val, max_val = get_spread_range_from_data(df_raw)

    with st.sidebar:
        st.divider()
        st.subheader("Spread Filters")
        spread_filter = st.slider(
            "Spread Range", 
            float(min_val), float(max_val), 
            (float(min_val), float(max_val)), 
            0.5,
            help="Filter data to only show games where the spread falls within this range."
        )

    global_herd_dist = get_global_herd_distribution(selected_seasons)
    df_global_stats = get_global_game_stats(selected_seasons)

    tab1, tab2 = st.tabs(["Spread Analysis", "User Performance"])
    
    # --- TAB 1: SPREAD ANALYSIS ---
    with tab1:
        st.markdown("### How to use this tab")
        st.markdown("""
        This tab provides a **League-Wide View**. It analyzes how the entire pool behaves against the spread.
        Use this to find market inefficiencies or to see if the pool has a bias towards favorites or underdogs.
        """)
        st.markdown("""
        Everything is in terms of the **Home Team**. A negative spread means the Home Team is favored (points are "taken away" from the home team), and a positive spread means the Away Team is favored (points are "given" to the home team). If a user is listed as "making a pick" for a certain spread, they are picking the home team, otherwise they are picking the away team.
        """)
        st.divider()

        with st.sidebar:
            bin_size = st.select_slider("Bin Size", options=[0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0], value=0.5)

        if not df_raw.empty:
            df_filtered = df_raw[
                (df_raw['home_spread'].round(1) >= spread_filter[0]) & 
                (df_raw['home_spread'].round(1) <= spread_filter[1])
            ].copy()
            
            total_picks = df_filtered['total_picks_made'].sum()
            avg_pick_home = (df_filtered['total_home_picks'].sum() / total_picks * 100) if total_picks > 0 else 0
            avg_cover_home = (df_filtered['total_covers'].sum() / df_filtered['total_games'].sum() * 100) if not df_filtered.empty else 0
            
            st.markdown(f"#### Aggregate Stats ({', '.join(map(str, selected_seasons))})")
            st.caption("Summary of all picks made across the selected seasons. Utilize the \"Spread Range\" filter in the sidebar to identify discrepancies between picks and actual outcomes.")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Users Picked Home", f"{avg_pick_home:.1f}%")
            m2.metric("Home Covered", f"{avg_cover_home:.1f}%")
            m3.metric("Bias", f"{avg_pick_home - avg_cover_home:+.1f}%", help="Positive = Pool overrates Home. Negative = Pool underrates Home.")
            m4.metric("Total Games", f"{df_filtered['total_games'].sum()}")
            st.divider()
            
            df_display = rebin_data(df_filtered, bin_size)
            st.markdown(f"#### Breakdown by Spread (Bin: {bin_size})")

            melted = df_display.melt(id_vars=['spread_bin', 'home_spread'], value_vars=['pct_picks_home', 'pct_games_home_covered'], var_name='Metric', value_name='Pct')
            melted['Metric'] = melted['Metric'].map({'pct_picks_home': 'User Pick %', 'pct_games_home_covered': 'Cover %'})
            
            st.subheader("1. Comparison: User Picks vs. Actual Outcomes")
            st.caption("Does the pool have a bias for certain spreads (orange), and is it justified (blue)? 'Picking' a spread in this instance means choosing the home team. 'Not Picking' means choosing the away team.")
            c1 = alt.Chart(melted).mark_bar().encode(
                x=alt.X('spread_bin', title='Spread Range', sort=alt.EncodingSortField(field="home_spread")),
                y=alt.Y('Pct', scale=alt.Scale(domain=[0, 100])),
                color=alt.Color('Metric', scale=alt.Scale(scheme='category10')), 
                xOffset='Metric',
                tooltip=['spread_bin', 'Metric', alt.Tooltip('Pct', format='.1f')]
            ).properties(height=350)
            st.altair_chart(c1, width="stretch")

            st.subheader("2. Bias Analysis: (User Pick % - Actual Cover %)")
            st.caption("Quantifying the error. Are we consistently wrong about certain spreads?")
            st.markdown("""
            * **Positive Bars (>0):** Users **Overestimated** the Home Team.
            * **Negative Bars (<0):** Users **Underestimated** the Home Team.
            * **Color Intensity:** Darker Green = Larger Sample Size.
            """)

            df_display['Diff'] = df_display['pct_picks_home'] - df_display['pct_games_home_covered']
            c2 = alt.Chart(df_display).mark_bar().encode(
                x=alt.X('spread_bin', title='Spread Range', sort=alt.EncodingSortField(field="home_spread")),
                y=alt.Y('Diff', title='Difference (pp)'),
                color=alt.Color('total_games', title='Sample Size', scale=alt.Scale(scheme='greens')),
                tooltip=['spread_bin', alt.Tooltip('Diff', format='.1f'), 'total_games']
            ).properties(height=350)
            st.altair_chart(c2, width="stretch")
            
            with st.expander("View Spread Data Table"):
                st.dataframe(
                    df_display[['spread_bin', 'total_games', 'pct_picks_home', 'pct_games_home_covered']].rename(columns={
                        'spread_bin': 'Spread', 'pct_picks_home': 'Pick %', 'pct_games_home_covered': 'Cover %'
                    }).style.format({'Pick %': '{:.1f}%', 'Cover %': '{:.1f}%'}),
                    width='stretch'
                )
        else:
            st.info("No data available for the selected seasons.")

    # --- TAB 2: USER PERFORMANCE ---
    with tab2:
        st.markdown("### How to use this tab")
        st.markdown("""
        Use this tab to analyze the picking behavior and performance of individual users, including a "Median Picker" that represents the most popular pick for each game.
        
        **Quick Links:**
        * [Which teams does this user predict best?](#detailed-team-breakdown)
        * [Does this user often pick the favorite, or the underdog?](#betting-style-bias-analysis)
        * [Is this user lucky or skilled?](#the-luck-spectrum)
        * [Does this user follow the crowd, or are they a lone wolf?](#herd-mentality-analysis)
        * [How accurate is this user's Monday Night point total prediction?](#mnf-totals-over-under-analysis)
        """)
        st.divider()

        user_list = get_unique_users()
        col_u, col_x = st.columns([1, 3])
        with col_u:
            selected_user = st.selectbox("Select User", user_list)

        if selected_user:
            df = get_user_performance_data(selected_user, selected_seasons)
            
            if not df.empty:
                df['user_won'] = df['pick_home'] == df['home_cover']
                df['raw_margin'] = (df['home_score'] + df['home_spread']) - df['away_score']
                df['user_margin'] = df.apply(lambda x: x['raw_margin'] if x['pick_home'] else -x['raw_margin'], axis=1)

                def get_herd_status(row):
                    p_home = row['home_pick_pct'] / 100.0 if row['home_pick_pct'] > 1 else row['home_pick_pct']
                    if row['pick_home']:
                        if p_home >= 0.60: return "Herd (Chalk)"
                        elif p_home <= 0.40: return "Contrarian (Lone Wolf)"
                        else: return "Neutral"
                    else:
                        if p_home <= 0.40: return "Herd (Chalk)"
                        elif p_home >= 0.60: return "Contrarian (Lone Wolf)"
                        else: return "Neutral"
                
                df['herd_status'] = df.apply(get_herd_status, axis=1)

                # =========================================================
                # 1. DETAILED TEAM BREAKDOWN
                # =========================================================
                st.subheader("1. Detailed Team Breakdown", anchor="detailed-team-breakdown")
                st.caption("A deep dive into user performance by specific NFL team.")
                
                all_teams = set(df['home_team_id']) | set(df['away_team_id'])
                team_stats = {t: {'for_win':0, 'for_loss':0, 'against_win':0, 'against_loss':0} for t in all_teams}
                
                for _, row in df.iterrows():
                    u_won = row['user_won']
                    h_team, a_team = row['home_team_id'], row['away_team_id']
                    if row['pick_home']:
                        if u_won: team_stats[h_team]['for_win']+=1
                        else: team_stats[h_team]['for_loss']+=1
                        if u_won: team_stats[a_team]['against_win']+=1 
                        else: team_stats[a_team]['against_loss']+=1
                    else: 
                        if u_won: team_stats[a_team]['for_win']+=1
                        else: team_stats[a_team]['for_loss']+=1
                        if u_won: team_stats[h_team]['against_win']+=1
                        else: team_stats[h_team]['against_loss']+=1
                
                df_user_stats = pd.DataFrame.from_dict(team_stats, orient='index').reset_index().rename(columns={'index':'Team'})
                df_user_stats['total_games'] = df_user_stats['for_win'] + df_user_stats['for_loss'] + df_user_stats['against_win'] + df_user_stats['against_loss']
                df_user_stats = df_user_stats[df_user_stats['total_games'] > 0]
                
                df_user_stats['user_wins'] = df_user_stats['for_win'] + df_user_stats['against_win']
                df_user_stats['team_covers'] = df_user_stats['for_win'] + df_user_stats['against_loss']
                df_user_stats['times_picked_for'] = df_user_stats['for_win'] + df_user_stats['for_loss']

                def calc_pct(num, den): return (num / den.replace(0, 1)) * 100
                df_user_stats['pct_user_win'] = calc_pct(df_user_stats['user_wins'], df_user_stats['total_games'])
                df_user_stats['pct_team_cover'] = calc_pct(df_user_stats['team_covers'], df_user_stats['total_games'])
                df_user_stats['pct_picked_for'] = calc_pct(df_user_stats['times_picked_for'], df_user_stats['total_games'])

                sort_options = {
                    "Highest User Win %": ("pct_user_win", "descending", "User Win %"),
                    "Lowest User Win %": ("pct_user_win", "ascending", "User Win %"),
                    "Most Picked For %": ("pct_picked_for", "descending", "Pick For %"),
                    "Least Picked For %": ("pct_picked_for", "ascending", "Pick For %"),
                    "Best Team ATS %": ("pct_team_cover", "descending", "Team Cover %"),
                    "Worst Team ATS %": ("pct_team_cover", "ascending", "Team Cover %"),
                    "Alphabetical (A-Z)": ("Team", "ascending", "User Win %")
                }
                
                c_sort, c_empty = st.columns([1, 2])
                with c_sort:
                    sort_choice = st.selectbox("Sort Teams By:", options=list(sort_options.keys()))
                
                sort_field, sort_order, display_label = sort_options[sort_choice]
                metric_to_display = "pct_user_win" if sort_field == "Team" else sort_field
                df_user_stats['DisplayMetric'] = df_user_stats[metric_to_display].apply(lambda x: f"{x:.1f}%")

                df_melted = df_user_stats.melt(
                    id_vars=['Team', 'total_games', 'pct_user_win', 'pct_team_cover', 'pct_picked_for', 'DisplayMetric'], 
                    value_vars=['for_win', 'for_loss', 'against_win', 'against_loss'],
                    var_name='Outcome', value_name='Count'
                )
                df_melted['PlotValue'] = df_melted.apply(lambda row: -row['Count'] if 'against' in row['Outcome'] else row['Count'], axis=1)
                
                outcome_labels = {'against_win': 'Picked Against (Won)', 'against_loss': 'Picked Against (Lost)', 'for_win': 'Picked For (Won)', 'for_loss': 'Picked For (Lost)'}
                df_melted['Label'] = df_melted['Outcome'].map(outcome_labels)
                color_scale = alt.Scale(domain=['Picked Against (Won)', 'Picked Against (Lost)', 'Picked For (Won)', 'Picked For (Lost)'], range=['#ff4b4b', '#8b0000', '#00c853', '#1b5e20'])

                bar_chart = alt.Chart(df_melted).mark_bar().encode(
                    y=alt.Y('Team', sort=alt.EncodingSortField(field=sort_field, order=sort_order), axis=alt.Axis(title=None)),
                    x=alt.X('PlotValue', title='Picks (Left=Against, Right=For)', axis=alt.Axis(tickMinStep=1)),
                    color=alt.Color('Label', scale=color_scale, title="Outcome"),
                    order=alt.Order('Outcome', sort='descending'),
                    tooltip=['Team', 'Label', alt.Tooltip('Count', title="Games")]
                ).properties(height=max(500, len(df_user_stats) * 25))

                text_chart = alt.Chart(df_melted).transform_filter(alt.datum.Outcome == 'for_win').mark_text(align='left', baseline='middle', dx=5).encode(
                    y=alt.Y('Team', sort=alt.EncodingSortField(field=sort_field, order=sort_order), axis=None),
                    text=alt.Text('DisplayMetric'),
                    color=alt.value('white')
                ).properties(title=display_label, width=60, height=max(500, len(df_user_stats) * 25))

                st.altair_chart(alt.hconcat(bar_chart, text_chart).configure_axis(labelFontSize=12, titleFontSize=14), width="stretch")


                # =========================================================
                # 2. BETTING STYLE
                # =========================================================
                st.divider()
                st.subheader("2. Betting Style (Bias Analysis)", anchor="betting-style-bias-analysis")
                st.caption(f"Analyzing picks where the spread was between **{spread_filter[0]}** and **{spread_filter[1]}**.")
                u_bin_size = st.slider("Spread Bin Size", 0.5, 5.0, 0.5, 0.5)

                df_bias = df[(df['home_spread'].round(1) >= spread_filter[0]) & (df['home_spread'].round(1) <= spread_filter[1])].copy()

                if not df_bias.empty:
                    df_bias['bin_floor'] = ((df_bias['home_spread'].round(1) // u_bin_size) * u_bin_size).round(1)
                    u_grouped = df_bias.groupby('bin_floor').agg({'game_id': 'count', 'pick_home': 'sum', 'home_cover': 'sum', 'home_spread': 'min'}).reset_index()
                    u_grouped['spread_bin'] = u_grouped['bin_floor'].apply(lambda x: f"{x:+.1f}") if u_bin_size == 0.5 else u_grouped.apply(lambda x: f"{x['bin_floor']:+.1f} to {x['bin_floor'] + u_bin_size - 0.1:+.1f}", axis=1)
                    u_grouped['User Pick Home %'] = (u_grouped['pick_home'] / u_grouped['game_id']) * 100
                    u_grouped['Actual Cover %'] = (u_grouped['home_cover'] / u_grouped['game_id']) * 100
                    u_grouped['Bias (Diff)'] = u_grouped['User Pick Home %'] - u_grouped['Actual Cover %']
                    u_grouped['Total Games'] = u_grouped['game_id']

                    u_melted = u_grouped.melt(id_vars=['spread_bin', 'home_spread'], value_vars=['User Pick Home %', 'Actual Cover %'], var_name='Metric', value_name='Pct')
                    
                    style_grouped = alt.Chart(u_melted).mark_bar().encode(
                        x=alt.X('spread_bin', title='Spread Range', sort=alt.EncodingSortField(field="home_spread")),
                        y=alt.Y('Pct', title='Percentage', scale=alt.Scale(domain=[0, 100])),
                        color=alt.Color('Metric', scale=alt.Scale(scheme='category10')),
                        xOffset='Metric',
                        tooltip=['spread_bin', 'Metric', alt.Tooltip('Pct', format='.1f')]
                    ).properties(height=300)
                    st.altair_chart(style_grouped, width="stretch")

                    bias_chart = alt.Chart(u_grouped).mark_bar().encode(
                        x=alt.X('spread_bin', title='Spread Range', sort=alt.EncodingSortField(field="home_spread")),
                        y=alt.Y('Bias (Diff)', title='Bias (User Pick % - Actual Cover %)'),
                        color=alt.Color('Total Games', title='Sample Size', scale=alt.Scale(scheme='greens')),
                        tooltip=['spread_bin', alt.Tooltip('Bias (Diff)', format='.1f'), 'Total Games']
                    ).properties(height=300)
                    st.altair_chart(bias_chart, width="stretch")
                else:
                    st.info("No games found in this spread range.")


                # =========================================================
                # 3. LUCK SPECTRUM (DUAL AXIS: PERCENT + COUNT)
                # =========================================================
                st.divider()
                st.subheader("3. The Luck Spectrum", anchor="the-luck-spectrum")
                st.caption("How often this user experiences lucky wins, bad beats, and blowout wins/losses compared to the pool average. White Ticks = Global Pool Average (in %). Colored Bars = Selected User's distribution.")
                
                luck_threshold = st.slider("Close Call Threshold (Points)", 0.5, 10.0, 2.5, 0.5)

                def classify_luck(margin):
                    if margin > luck_threshold: return f"Convincing Win (>{luck_threshold} pts)"
                    if margin > 0: return f"Lucky Win (≤ {luck_threshold} pts)"
                    if margin >= -luck_threshold: return f"Bad Beat (≤ {luck_threshold} pts)"
                    return f"Blowout Loss (>{luck_threshold} pts)"

                df['luck_bucket'] = df['user_margin'].apply(classify_luck)
                luck_counts = df['luck_bucket'].value_counts().reset_index()
                luck_counts.columns = ['Bucket', 'Count']
                luck_counts['Percent'] = (luck_counts['Count'] / len(df)) * 100
                
                luck_order = [f"Blowout Loss (>{luck_threshold} pts)", f"Bad Beat (≤ {luck_threshold} pts)", f"Lucky Win (≤ {luck_threshold} pts)", f"Convincing Win (>{luck_threshold} pts)"]
                luck_colors = ['#8b0000', '#ff4b4b', '#00c853', '#1b5e20']

                # Global Stats
                pool_luck_counts = {k: 0.0 for k in luck_order}
                total_global_weight = 0
                if not df_global_stats.empty:
                    df_finished = df_global_stats.dropna(subset=['home_margin'])
                    for _, row in df_finished.iterrows():
                        h_bucket = classify_luck(row['home_margin'])
                        a_bucket = classify_luck(-row['home_margin'])
                        p_home = row['home_pick_pct'] if pd.notnull(row['home_pick_pct']) else 50.0
                        pool_luck_counts[h_bucket] += p_home
                        pool_luck_counts[a_bucket] += (100.0 - p_home)
                        total_global_weight += 100.0

                pool_display_data = []
                if total_global_weight > 0:
                    for bucket in luck_order:
                        pool_display_data.append({'Bucket': bucket, 'Pool Pct': (pool_luck_counts[bucket] / total_global_weight) * 100})
                
                df_pool_luck = pd.DataFrame(pool_display_data)

                user_total_games = len(df)
                max_pct = 0
                if not luck_counts.empty: max_pct = luck_counts['Percent'].max()
                if not df_pool_luck.empty: max_pct = max(max_pct, df_pool_luck['Pool Pct'].max())
                
                domain_max_pct = math.ceil(max_pct * 1.1)
                domain_max_count = math.ceil(domain_max_pct * user_total_games / 100)

                base_luck = alt.Chart(luck_counts).encode(y=alt.Y('Bucket:N', sort=luck_order, title=None))
                
                bars = base_luck.mark_bar().encode(
                    x=alt.X('Percent:Q', title='Percentage', scale=alt.Scale(domain=[0, domain_max_pct])),
                    color=alt.Color('Bucket', scale=alt.Scale(domain=luck_order, range=luck_colors), legend=None),
                    tooltip=['Bucket:N', 'Count:Q', alt.Tooltip('Percent:Q', format='.1f')]
                )
                
                pool_ticks = alt.Chart(df_pool_luck).mark_tick(
                    color='white', thickness=4, height=40
                ).encode(
                    y=alt.Y('Bucket:N', sort=luck_order),
                    x=alt.X(
                        'Pool Pct:Q',
                        scale=alt.Scale(domain=[0, domain_max_pct]),
                        axis=None
                    ),
                    tooltip=[alt.Tooltip('Pool Pct:Q', format='.1f', title='Pool Average %')]
                )

                dummy_counts = base_luck.mark_circle(opacity=0).encode(
                    x=alt.X(
                        'Count:Q',
                        title='Number of Games',
                        axis=alt.Axis(
                            orient='top',
                            tickCount=5,
                            tickMinStep=1,
                            titlePadding=20
                        ),
                        scale=alt.Scale(domain=[0, domain_max_count])
                    )
                )

                st.altair_chart((bars + pool_ticks + dummy_counts).resolve_scale(x='independent'), width="stretch")

                # =========================================================
                # 4. HERD MENTALITY (DUAL AXIS)
                # =========================================================
                st.divider()
                st.subheader("4. Herd Mentality Analysis", anchor="herd-mentality-analysis")
                st.markdown(
                    "<small>"
                    "How often this user picks with the crowd vs. against it, and their success.<br>"
                    "White Line = Global Average (%). Bars = Selected User's distribution (# of games).<br>"
                    "Global Average excludes games where users did not submit picks, causing slight logical discrepancies, particularly in the 'Neutral' category."
                    "</small>",
                    unsafe_allow_html=True
                )

             

                herd_df = df.groupby(['herd_status', 'user_won']).size().reset_index(name='count')
                herd_df['Result'] = herd_df['user_won'].map({True: 'Won', False: 'Lost'})
                
                herd_totals = herd_df.groupby('herd_status')['count'].sum().reset_index(name='total')
                herd_totals['Percent'] = (herd_totals['total'] / len(df)) * 100

                herd_final = pd.merge(herd_df, herd_totals[['herd_status', 'Percent']], on='herd_status')
                herd_final['BarLength'] = (herd_final['count'] / len(df)) * 100

                herd_wins = herd_df[herd_df['user_won'] == True].groupby('herd_status')['count'].sum().reset_index(name='wins')
                stats_merge = pd.merge(herd_totals, herd_wins, on='herd_status', how='left').fillna(0)
                stats_merge['Win Pct'] = (stats_merge['wins'] / stats_merge['total']) * 100
                herd_final = pd.merge(herd_final, stats_merge[['herd_status', 'Win Pct']], on='herd_status')

                pool_herd_counts = {'Herd (Chalk)': 0.0, 'Contrarian (Lone Wolf)': 0.0, 'Neutral': 0.0}
                pool_herd_wins = {'Herd (Chalk)': 0.0, 'Contrarian (Lone Wolf)': 0.0, 'Neutral': 0.0}
                total_global_herd_weight = 0
                if not df_global_stats.empty:
                    df_herd_calc = df_global_stats.dropna(subset=['home_margin'])
                    for _, row in df_herd_calc.iterrows():
                        p_home = row['home_pick_pct'] if pd.notnull(row['home_pick_pct']) else 50.0
                        p_away = 100.0 - p_home
                        home_covers = row['home_margin'] > 0
                        
                        # Determine Categories
                        cat_home = 'Herd (Chalk)' if p_home > 60 else 'Contrarian (Lone Wolf)' if p_home < 40 else 'Neutral'
                        cat_away = 'Herd (Chalk)' if p_away > 60 else 'Contrarian (Lone Wolf)' if p_away < 40 else 'Neutral'
                        
                        # Add to Totals (Volume)
                        pool_herd_counts[cat_home] += p_home
                        pool_herd_counts[cat_away] += p_away
                        
                        # Add to Wins
                        if home_covers:
                            pool_herd_wins[cat_home] += p_home
                        else:
                            pool_herd_wins[cat_away] += p_away
                            
                        total_global_herd_weight += 100.0

                pool_herd_display = []
                if total_global_herd_weight > 0:
                    for cat in pool_herd_counts:
                        total_vol = pool_herd_counts[cat]
                        # Calculate Win % for this category
                        win_rate = (pool_herd_wins[cat] / total_vol * 100) if total_vol > 0 else 0.0
                        
                        pool_herd_display.append({
                            'herd_status': cat, 
                            'Pool Pct': (total_vol / total_global_herd_weight) * 100,
                            'Pool Win Rate': win_rate
                        })
                
                df_pool_herd = pd.DataFrame(pool_herd_display)

                max_h_pct = 0
                if not herd_totals.empty: max_h_pct = herd_totals['Percent'].max()
                if not df_pool_herd.empty: max_h_pct = max(max_h_pct, df_pool_herd['Pool Pct'].max())
                
                h_domain_pct = math.ceil(max_h_pct * 1.1)
                h_domain_count = math.ceil(h_domain_pct * len(df) / 100)
                h_pct_scale = alt.Scale(domain=[0, h_domain_pct])


                h_base = alt.Chart(herd_final).encode(y=alt.Y('herd_status:N', title=None))
                
                h_chart = h_base.mark_bar().encode(
                    x=alt.X(
                        'BarLength:Q',
                        title='Percentage',
                        scale=h_pct_scale
                    ),
                    color=alt.Color(
                        'Result',
                        scale=alt.Scale(domain=['Won', 'Lost'], range=['#00c853', '#ff4b4b'])
                    ),
                    tooltip=[
                        'herd_status:N',
                        'Result:N',
                        'count:Q',
                        alt.Tooltip('Win Pct:Q', format='.1f', title="Status Win %")
                    ]
                )


                h_ticks = alt.Chart(df_pool_herd).mark_tick(
                    color='white', thickness=4, height=40
                ).encode(
                    y=alt.Y('herd_status:N', title=None),
                    x=alt.X(
                        'Pool Pct:Q',
                        scale=h_pct_scale,
                        axis=None
                    ),
                    tooltip=[
                        alt.Tooltip('Pool Pct:Q', format='.1f', title='Pool Frequency %'),
                        alt.Tooltip('Pool Win Rate:Q', format='.1f', title='Avg Win Rate %')
                    ]
                )

                h_dummy = h_base.mark_circle(opacity=0).encode(
                    x=alt.X('count:Q', 
                            title='Games Picked', 
                            axis=alt.Axis(orient='top', tickCount=5, tickMinStep=1, titlePadding=20), 
                            scale=alt.Scale(domain=[0, h_domain_count]))
                )
                
                st.altair_chart((h_chart + h_ticks + h_dummy).resolve_scale(x='independent'), width="stretch")


                # =========================================================
                # 5. MNF TOTALS ANALYSIS
                # =========================================================
                st.divider()
                st.subheader("5. MNF Totals (Combined Score) Analysis", anchor="mnf-totals-over-under-analysis")
                df_mnf = df[df['mnf'] == True].copy()

                if not df_mnf.empty:
                    stats_df = df_mnf[(df_mnf['actual_total'] > 0) & (df_mnf['tot_if_picked'].notnull()) & (df_mnf['pool_median_total'].notnull())].copy()
                    if not stats_df.empty:
                        stats_df['abs_err_user'] = abs(stats_df['tot_if_picked'] - stats_df['actual_total'])
                        stats_df['abs_err_median'] = abs(stats_df['pool_median_total'] - stats_df['actual_total'])
                        stats_df['bias_user'] = stats_df['tot_if_picked'] - stats_df['actual_total']
                        
                        mae_user = stats_df['abs_err_user'].mean()
                        mae_median = stats_df['abs_err_median'].mean()
                        me_user = stats_df['bias_user'].mean()
                        edge_pct = ((mae_median - mae_user) / mae_median) * 100 if mae_median > 0 else 0.0
                        wins = len(stats_df[stats_df['abs_err_user'] < stats_df['abs_err_median']])
                        losses = len(stats_df[stats_df['abs_err_user'] > stats_df['abs_err_median']])
                        ties = len(stats_df[stats_df['abs_err_user'] == stats_df['abs_err_median']])
                        
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("Your MAE (Accuracy)", f"{mae_user:.1f}", delta=f"{edge_pct:+.1f}% Edge", delta_color="normal")
                        c2.metric("Median Picker MAE", f"{mae_median:.1f}")
                        c3.metric("Your Bias (Mean Error)", f"{me_user:+.1f}")
                        c4.metric("Record vs. Median", f"{wins}-{losses}-{ties}")
                    else:
                        st.caption("Not enough completed games for summary statistics.")

                    df_mnf['season_week_label'] = df_mnf.apply(lambda x: f"{x['season']} W{x['week']}", axis=1)
                    df_mnf['season_week_sort'] = df_mnf['season'] * 100 + df_mnf['week']

                    u_dots = df_mnf[['season_week_label', 'season_week_sort', 'tot_if_picked', 'game_id']].dropna(subset=['tot_if_picked']).copy()
                    u_dots['Type'] = 'User Pick'
                    u_dots['Value'] = u_dots['tot_if_picked']
                    p_dots = df_mnf[['season_week_label', 'season_week_sort', 'pool_median_total', 'game_id']].dropna(subset=['pool_median_total']).copy()
                    p_dots['Type'] = 'Pool Median'
                    p_dots['Value'] = p_dots['pool_median_total']
                    a_dots = df_mnf[['season_week_label', 'season_week_sort', 'actual_total', 'game_id']].dropna(subset=['actual_total']).copy()
                    a_dots['Type'] = 'Actual Score'
                    a_dots['Value'] = a_dots['actual_total']
                    a_dots = a_dots[a_dots['Value'] > 0]

                    df_all_dots = pd.concat([u_dots, p_dots, a_dots])
                    type_scale = alt.Scale(domain=['User Pick', 'Pool Median', 'Actual Score'], range=['#2979ff', '#bdbdbd', '#d50000'])
                    base_x = alt.X('season_week_label', sort=alt.EncodingSortField(field='season_week_sort', order='ascending'), title='Week')

                    df_pivot = df_all_dots.pivot_table(index=['season_week_label', 'season_week_sort', 'game_id'], columns='Type', values='Value').reset_index()
                    if 'User Pick' in df_pivot.columns and 'Actual Score' in df_pivot.columns:
                        rule_chart = alt.Chart(df_pivot).mark_rule(color='#e0e0e0', strokeWidth=2).encode(x=base_x, y='User Pick', y2='Actual Score')
                    else:
                        rule_chart = alt.Chart(df_all_dots).mark_rule(opacity=0).encode(x=base_x)

                    chart_actual = alt.Chart(a_dots).mark_point(size=300, filled=True, opacity=0.3, shape='circle').encode(
                        x=base_x, y=alt.Y('Value', title='Total Points', scale=alt.Scale(zero=False)), color=alt.Color('Type', scale=type_scale, title="Legend"), tooltip=['season_week_label', 'Type', 'Value']
                    )
                    chart_median = alt.Chart(p_dots).mark_point(size=120, filled=True, opacity=0.8, shape='circle').encode(
                        x=base_x, y='Value', color=alt.Color('Type', scale=type_scale), tooltip=['season_week_label', 'Type', 'Value']
                    )
                    chart_user = alt.Chart(u_dots).mark_point(size=50, filled=True, opacity=1.0, shape='circle').encode(
                        x=base_x, y='Value', color=alt.Color('Type', scale=type_scale), tooltip=['season_week_label', 'Type', 'Value']
                    )

                    st.altair_chart((rule_chart + chart_actual + chart_median + chart_user).interactive(), width="stretch")
                else:
                    st.info("No MNF data found for this selection.")

            else:
                st.warning("No data for this user.")