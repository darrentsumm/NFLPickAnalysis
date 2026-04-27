import os
import json
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client
from agent import PickerAgent  # Make sure your PickerAgent class is in agent.py

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

def fetch_2025_validation_data():
    """Fetches the 2025 games, context, and actual IRL picks."""
    print("Fetching 2025 validation data...")
    
    # 1. Get 2025 Games (Including ties!)
    games_res = supabase.table('game').select(
        'game_id, home_team_id, away_team_id, home_spread, mnf, home_score, away_score, tie_spread'
    ).eq('season', 2025).execute()
    df_games = pd.DataFrame(games_res.data)
    df_games['game_id'] = df_games['game_id'].astype(str)
    
    # 2. Get Momentum
    recency_res = supabase.table('game_recency_stats').select('game_id, home_su_win_pct_l3, away_su_win_pct_l3').execute()
    df_recency = pd.DataFrame(recency_res.data)
    df_recency['game_id'] = df_recency['game_id'].astype(str)
    df_games = pd.merge(df_games, df_recency, on='game_id', how='left')
    
    # 3. Get 2025 Picks & Consensus (WITH BATCHING!)
    target_game_ids = df_games['game_id'].tolist()
    all_picks = []
    
    # Fetch picks in chunks of 100 to avoid Supabase URL length limits
    for i in range(0, len(target_game_ids), 5):
        chunk = target_game_ids[i:i+5]
        picks_res = supabase.table('pick').select(
            'game_id, username, pick_home, pick_made, pick_overwritten'
        ).in_('game_id', chunk).execute()
        
        all_picks.extend(picks_res.data)
        
    df_picks = pd.DataFrame(all_picks)
    
    # Sanitize IDs just to be safe
    df_picks['game_id'] = df_picks['game_id'].astype(str).str.split('.').str[0]
    
    # Filter picks to only the matching games (This will now work perfectly!)
    df_picks = df_picks[df_picks['game_id'].isin(df_games['game_id'])]
    
    # Consensus
    consensus = df_picks[df_picks['pick_made'] == True].groupby('game_id').agg(
        total_picks=('pick_home', 'count'),
        home_picks=('pick_home', 'sum')
    ).reset_index()
    consensus['public_home_pct'] = consensus['home_picks'] / consensus['total_picks']
    df_games = pd.merge(df_games, consensus[['game_id', 'public_home_pct']], on='game_id', how='left')
    
    # Cleanup NAs
    df_games['home_su_win_pct_l3'] = df_games['home_su_win_pct_l3'].fillna(0.5)
    df_games['away_su_win_pct_l3'] = df_games['away_su_win_pct_l3'].fillna(0.5)
    df_games['public_home_pct'] = df_games['public_home_pct'].fillna(0.5)
    
    return df_games, df_picks

def run_historical_validation(simulations_per_user=100):
    """
    Tests agents against their IRL counterparts using a Monte Carlo approach.
    Since agents use random(), we run them 100 times to get their true average logic.
    """
    df_games, df_picks = fetch_2025_validation_data()

    with open('agent_profiles.json', 'r') as f:
        profiles = json.load(f)
        
    results = []
    
    # Isolate only users who actually played in 2025
    active_2025_users = df_picks[df_picks['pick_made'] == True]['username'].unique()
    print(f"Validating {len(active_2025_users)} agents...")

    for username in active_2025_users:
        if username not in profiles:
            continue
            
        # 1. Initialize Agent and FORCE forgetfulness to 0 for validation
        profile = profiles[username]
        agent = PickerAgent(username, profile)
        agent.forgetfulness_rate = 0.0  
        
        # 2. Get this user's valid IRL picks
        u_picks = df_picks[
            (df_picks['username'] == username) & 
            (df_picks['pick_made'] == True) & 
            (df_picks['pick_overwritten'] == False)
        ]
        
        if u_picks.empty:
            continue
            
        total_valid_games = len(u_picks)
        
        # Trackers for the Monte Carlo loops
        total_behavior_matches = 0
        total_sim_wins = 0
        total_irl_wins = 0 # This only needs to be calculated once, but we'll track it
        
        # Run the agent through their 2025 schedule multiple times to find the average behavior
        for _ in range(simulations_per_user):
            for _, pick_row in u_picks.iterrows():
                # Find the game context
                game = df_games[df_games['game_id'] == pick_row['game_id']].iloc[0]
                
                # Format context for the agent
                game_context = {
                    'spread': game['home_spread'],
                    'home_team': game['home_team_id'],
                    'away_team': game['away_team_id'],
                    'home_momentum': game['home_su_win_pct_l3'],
                    'away_momentum': game['away_su_win_pct_l3'],
                    'public_home_pct': game['public_home_pct'],
                    'is_mnf': game['mnf'],
                    'vegas_total': 44.5 
                }
                
                # 3. Agent makes a pick
                sim_pick, _ = agent.decide_pick(game_context)
                irl_pick = "HOME" if pick_row['pick_home'] else "AWAY"
                
                # 4. Determine Actual Winner
                raw_margin = (game['home_score'] + game['home_spread']) - game['away_score']
                if raw_margin > 0: actual_cover = "HOME"
                elif raw_margin < 0: actual_cover = "AWAY"
                else: actual_cover = "PUSH"
                
                # 5. Grade the Agent vs IRL
                if sim_pick == irl_pick:
                    total_behavior_matches += 1
                    
                # 6. Grade the Agent vs Reality (Ignoring Pushes)
                if actual_cover != "PUSH":
                    if sim_pick == actual_cover: total_sim_wins += 1
                    if irl_pick == actual_cover: total_irl_wins += 1

        # Calculate Averages across the simulations
        avg_matches = total_behavior_matches / simulations_per_user
        avg_sim_wins = total_sim_wins / simulations_per_user
        irl_wins = total_irl_wins / simulations_per_user # Div by sim count just scales it back to 1x
        
        # We only calculate win % on games that weren't pushes
        pushes = len(df_games[df_games['tie_spread'] == True])
        win_denominator = total_valid_games - pushes
        if win_denominator <= 0: win_denominator = 1
        
        results.append({
            'username': username,
            'total_games_picked': total_valid_games,
            'behavior_match_pct': round((avg_matches / total_valid_games) * 100, 1),
            'sim_win_pct': round((avg_sim_wins / win_denominator) * 100, 1),
            'irl_win_pct': round((irl_wins / win_denominator) * 100, 1)
        })

    # Output Results
    results_df = pd.DataFrame(results)
    print("\n--- Validation Results ---")
    print(results_df.describe())
    
    # Save to CSV for you to analyze in Tableau or Excel
    results_df.to_csv('2025_validation_results.csv', index=False)
    print("\nDetailed results saved to 2025_validation_results.csv")

if __name__ == "__main__":
    run_historical_validation()