import os
import json
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client
import numpy as np

# --- 1. Setup ---
load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

def fetch_data():
    """Fetches all games, picks, and momentum stats into memory."""
    print("Fetching games...")
    # FIX: Add 'season' to the select string
    games_res = supabase.table('game').select('game_id, home_team_id, away_team_id, home_spread, mnf, home_score, away_score, season').execute()
    df_games = pd.DataFrame(games_res.data)
    df_games['actual_total'] = df_games['home_score'] + df_games['away_score']
    
    print("Fetching recency stats...")
    recency_res = supabase.table('game_recency_stats').select('game_id, home_su_win_pct_l3, away_su_win_pct_l3').execute()
    df_recency = pd.DataFrame(recency_res.data)
    
    # Merge the recency data directly into the games dataframe
    # Ensure game_ids are strings to prevent Pandas merge failures!
    df_games['game_id'] = df_games['game_id'].astype(str)
    df_recency['game_id'] = df_recency['game_id'].astype(str)
    df_games = pd.merge(df_games, df_recency, on='game_id', how='left')
    
    print("Fetching picks...")
    picks_res = supabase.table('pick').select('game_id, username, pick_home, pick_made, tot_if_picked, pick_overwritten').execute()
    df_picks = pd.DataFrame(picks_res.data)
    df_picks['game_id'] = df_picks['game_id'].astype(str)
    
    return df_games, df_picks

def calculate_spread_bin(spread):
    if pd.isna(spread): return 'unknown'
    if spread <= -10.0: return 'heavy_home_fav'
    elif spread <= -7.0: return 'solid_home_fav'
    elif spread <= -3.5: return 'mod_home_fav'
    elif spread <= -0.5: return 'slight_home_fav'
    elif spread <= 3.0: return 'slight_away_fav'
    elif spread <= 6.5: return 'mod_away_fav'
    elif spread <= 9.5: return 'solid_away_fav'
    else: return 'heavy_away_fav'

def build_profiles():
    df_games, df_picks = fetch_data()
    
    # Merge games and picks
    df = pd.merge(df_picks, df_games, on='game_id', how='inner')
    df['spread_bin'] = df['home_spread'].apply(calculate_spread_bin)
    
    # --- Calculate Global Consensus ---
    print("Calculating global consensus...")
    consensus = df[df['pick_made'] == True].groupby('game_id').agg(
        total_picks=('username', 'count'),
        home_picks=('pick_home', 'sum')
    ).reset_index()
    consensus['public_home_pct'] = consensus['home_picks'] / consensus['total_picks']
    df = pd.merge(df, consensus[['game_id', 'public_home_pct']], on='game_id', how='left')

    # Define Chalk Games (where public leans > 65% one way)
    df['is_chalk_game'] = (df['public_home_pct'] > 0.65) | (df['public_home_pct'] < 0.35)
    df['faded_public'] = False
    
    # Did they fade? (Picked Home when public < 35%, or picked Away when public > 65%)
    mask_fade_home = (df['pick_home'] == True) & (df['public_home_pct'] < 0.35)
    mask_fade_away = (df['pick_home'] == False) & (df['public_home_pct'] > 0.65)
    df.loc[mask_fade_home | mask_fade_away, 'faded_public'] = True

    # --- Calculate Global Bin Averages (For Fallbacks) ---
    global_bins = df[df['pick_made'] == True].groupby('spread_bin')['pick_home'].mean().to_dict()

    agents = {}
    users = df['username'].unique()
    print(f"Building profiles for {len(users)} users...")

    # --- NEW: Setup for Team Bias (Temporal Context) ---
    import numpy as np
    
    df['picked_team'] = np.where(df['pick_home'] == True, df['home_team_id'], df['away_team_id'])
    
    # Stack options, crucially keeping the 'season' column
    home_options = df[['game_id', 'season', 'home_team_id', 'pick_home', 'pick_made']].rename(columns={'home_team_id': 'team', 'pick_home': 'was_picked'})
    away_options = df[['game_id', 'season', 'away_team_id', 'pick_home', 'pick_made']].rename(columns={'away_team_id': 'team'})
    away_options['was_picked'] = ~away_options['pick_home']
    
    all_team_options = pd.concat([home_options, away_options])
    
    # STRICT RULE: If the user didn't make a pick, do not let it dilute the percentages
    all_team_options = all_team_options[all_team_options['pick_made'] == True]

    for user in users:
        u_df = df[df['username'] == user]
        
        # 1. Forgetfulness Rate
        total_eligible = len(u_df)
        missed = len(u_df[(u_df['pick_made'] == False) | (u_df['pick_overwritten'] == True)])
        forget_rate = missed / total_eligible if total_eligible > 0 else 0.05
        
        # Filter to only games they actually picked for the rest of the math
        picked_df = u_df[u_df['pick_made'] == True]
        if picked_df.empty:
            continue
            
        # 2. Contrarian Index
        chalk_games = picked_df[picked_df['is_chalk_game'] == True]
        if len(chalk_games) >= 5:
            contrarian_index = chalk_games['faded_public'].mean()
        else:
            contrarian_index = 0.25 # Default standard baseline
            
        # 3. MNF Bias
        mnf_df = picked_df[(picked_df['mnf'] == True) & (picked_df['tot_if_picked'].notnull()) & (picked_df['actual_total'].notnull())]
        if not mnf_df.empty:
            mnf_bias_pts = (mnf_df['tot_if_picked'] - mnf_df['actual_total']).mean()
        else:
            mnf_bias_pts = 0.0
            
        # 4. Recency Weight (Linear Regression)
        recency_df = picked_df.dropna(subset=['home_su_win_pct_l3', 'away_su_win_pct_l3']).copy()
        if len(recency_df) >= 10:  # Only calculate if we have a decent sample size
            recency_df['momentum_delta'] = recency_df['home_su_win_pct_l3'] - recency_df['away_su_win_pct_l3']
            x = recency_df['momentum_delta'].values
            y = recency_df['pick_home'].astype(int).values
            try:
                slope, intercept = np.polyfit(x, y, 1)
                recency_weight = float(slope)
            except:
                recency_weight = 0.0
        else:
            recency_weight = 0.0
            
        # 5. Team Bias (Homer / Hater)
        # ==========================================
        # NEW: 5. Team Bias (Temporal Homer / Hater)
        # ==========================================
        team_bias = {}
        
        # Figure out exactly which seasons this specific user made picks in
        active_seasons = picked_df['season'].unique()
        
        # Isolate the user's specific decisions
        u_options = all_team_options[all_team_options.index.isin(picked_df.index)]
        
        # Isolate the POOL'S decisions, but ONLY during the years this user was active!
        custom_pool_options = all_team_options[all_team_options['season'].isin(active_seasons)]
        
        if not u_options.empty:
            # Calculate the Custom Global Rate for this user's specific era
            custom_global_rates = custom_pool_options.groupby('team')['was_picked'].mean().to_dict()

            user_team_stats = u_options.groupby('team').agg(
                times_available=('was_picked', 'count'),
                pick_rate=('was_picked', 'mean')
            ).reset_index()
            
            for _, row in user_team_stats.iterrows():
                team = row['team']
                games_played = row['times_available']
                user_rate = row['pick_rate']
                
                if games_played >= 5:
                    # Look up the pool's rate during this user's era (default to 50% if somehow missing)
                    era_global_rate = custom_global_rates.get(team, 0.50)
                    delta = user_rate - era_global_rate
                    
                    if abs(delta) >= 0.20:
                        team_bias[team] = round(float(delta), 3)

        # 6. Spread Bins (With Fallback)
        user_bins = {}
        for bin_name in global_bins.keys():
            bin_data = picked_df[picked_df['spread_bin'] == bin_name]
            if len(bin_data) >= 5:
                user_bins[bin_name] = round(float(bin_data['pick_home'].mean()), 3)
            else:
                user_bins[bin_name] = round(float(global_bins[bin_name]), 3)

        # 7. Assemble the DNA
        agents[user] = {
            "forgetfulness": round(float(forget_rate), 3),
            "contrarian_index": round(float(contrarian_index), 3),
            "recency_weight": round(recency_weight, 3), 
            "mnf_bias_pts": round(float(mnf_bias_pts), 1),
            "spread_bins": user_bins,
            "team_bias": team_bias
        }

    # --- Save to JSON ---
    output_file = 'agent_profiles.json'
    with open(output_file, 'w') as f:
        json.dump(agents, f, indent=4)
        
    print(f"Success! {len(agents)} agent profiles saved to {output_file}")

if __name__ == "__main__":
    build_profiles()