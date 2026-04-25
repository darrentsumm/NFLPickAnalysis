import random

class PickerAgent:
    def __init__(self, username, profile_data):
        """
        Initializes a Digital Twin agent based on historical user data.
        
        :param username: str, The user's unique identifier.
        :param profile_data: dict, The calculated weights and biases from the database.
        """
        self.username = username
        
        # Simulation State Trackers (Reset every simulated season)
        self.season_points = 0
        self.strikes = 0  
        self.is_eliminated = False
        
        # --- Core Behavioral Traits ---
        self.forgetfulness_rate = profile_data.get('forgetfulness', 0.05)
        self.contrarian_index = profile_data.get('contrarian_index', 0.0)
        self.recency_weight = profile_data.get('recency_weight', 0.5)
        self.mnf_bias_pts = profile_data.get('mnf_bias_pts', 0.0)
        self.team_bias = profile_data.get('team_bias', {}) 
        
        # --- Spread Soft-Spot Bins (Prob to pick Home) ---
        # Defaults to 50% if the data is missing
        self.spread_bins = profile_data.get('spread_bins', {
            'heavy_home_fav': 0.50,
            'solid_home_fav': 0.50,
            'mod_home_fav': 0.50,
            'slight_home_fav': 0.50,
            'slight_away_fav': 0.50,
            'mod_away_fav': 0.50,
            'solid_away_fav': 0.50,
            'heavy_away_fav': 0.50
        })

    def decide_pick(self, game_context):
        """
        The core logic engine. Evaluates a game and returns 'HOME', 'AWAY', or 'NONE'.
        """
        # 1. Check for forgetfulness first (The Strike System)
        if random.random() < self.forgetfulness_rate:
            self.strikes += 1
            if self.strikes == 1:
                return "HOME" # Strike 1: Auto-Home
            else:
                self.is_eliminated = True
                return "NONE" # Strike 2+: Zeros
        
        # 2. If they remembered to pick, calculate the probability of picking Home
        # (We will build out this math next)
        base_prob = self._get_bin_probability(game_context['spread'])
        
        # Apply modifiers (Team bias, Recency, Contrarian)...
        final_prob = base_prob # Placeholder for the math
        
        # 3. Make the final choice
        if random.random() < final_prob:
            return "HOME"
        else:
            return "AWAY"
            
    def _get_bin_probability(self, spread):
        """Helper method to map the current spread to the agent's specific bin"""
        if spread <= -10.0: return self.spread_bins['heavy_home_fav']
        elif spread <= -7.0: return self.spread_bins['solid_home_fav']
        elif spread <= -3.5: return self.spread_bins['mod_home_fav']
        elif spread <= -0.5: return self.spread_bins['slight_home_fav']
        elif spread <= 3.0: return self.spread_bins['slight_away_fav']
        elif spread <= 6.5: return self.spread_bins['mod_away_fav']
        elif spread <= 9.5: return self.spread_bins['solid_away_fav']
        else: return self.spread_bins['heavy_away_fav']