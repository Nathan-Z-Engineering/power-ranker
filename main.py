from datetime import datetime
import json
from urllib.error import URLError

from graphqlclient import GraphQLClient
import gspread
import queries
from datamodels import *
from itertools import groupby
import pandas as pd


def collect_user_ids_from_file():
  """Reads through a text file and compiles a dictionary of user_id -> player_name."""

  with open('user-ids.txt', 'r') as file:
    delimiter = '---'
    delimiter2 = "***"
    for line in file:
      if line.startswith('#'):
        continue
       # Get player name and user id with discriminator
      name, user_id_discriminator = line.strip().split(delimiter)
        
        # Split user_id_discriminator to get user ID and discriminator
      user_id, discriminator = user_id_discriminator.split(delimiter2)
        
        # Add player name and user ID with discriminator to dictionary
      user_dict[user_id] = name
      user_discrim_dict[name] = discriminator


# Currently deprecated
def set_tournaments():
  """Runs a tournament query for each user that was collected.
  Returns a dictonary of tourney_slug -> TourneyObj.
  """
  tourney_dict = dict()
  for user_id, player_name in user_dict.items():
    print("Processing " + player_name + "'s tournaments...")
    query_variables = {"userId": user_id}

    result = execute_query(queries.get_tournies_by_user, query_variables)
    res_data = json.loads(result)
    if 'errors' in res_data:
        print('Error:')
        print(res_data['errors'])

    for tourney_json in res_data['data']['user']['tournaments']['nodes']:
      cut_off_date_start = datetime(2022, 10, 1)
      cut_off_date_end = datetime(2022, 12, 31)
      
      tourney = Tournament(tourney_json)
    
      str_date = tourney.start_time.strftime('%m-%d-%Y')
      
      if tourney.start_time >= cut_off_date_start and tourney.start_time <= cut_off_date_end:
        if tourney.is_online:
          continue
        print(tourney.name + '\t' + str_date)
        tourney_dict[tourney.slug] = tourney
      elif tourney.start_time < cut_off_date_start:
        print('Tournament outside of season window --- ' + tourney.name + '\t' + str_date)
        break
  
  return tourney_dict


def execute_query(query, variables):
  """Executes GraphQL queries. Cycles through multiple tokens to avoid request limits."""

  global client_idx
  global clients

  if client_idx == len(clients):
    client_idx = 0

  result = clients[client_idx].execute(query, variables)
  
  client_idx += 1
  
  return result

def get_placements(tournies):
  """Aquires all the tournament placements of tracked players and stores the results in a dictionary"""
  print("Getting placement data...")
  event_id_dict = {}
  # perPage <= 199 if greater query request will be too large
  # Thus we can only grab about the top 200 placements 
  variable = { "page": 1, "perPage": 199}

  discriminator_list = []
  for player in user_discrim_dict.values():
    discriminator_list.append(player)
  
  player_placements = {key: [] for key in discriminator_list}

  for tourney in tournies.values():
    name = tourney.name
    event_id_dict[name] = tourney.events[0].id
  # print(tourney)
  for key,event in event_id_dict.items():
    print("---"+ key)
    variable["eventId"] = event

    result = execute_query(queries.get_event_standings, variable)
    res_data = json.loads(result)
      
    standings_data = res_data['data']['event']['standings']['nodes']
  
    for key in player_placements:
      player_placements[key].append(None)

  # Group the standings data by discriminator value
    for standing in standings_data:

      #Error handeling for issues grabbing user data
      try:
        discriminator = standing['entrant']['participants'][0]['user']['discriminator']
        if discriminator in discriminator_list:
          player_placements[discriminator][-1] = standing['placement']
      except:
        pass
        # print("skip")
          
  # print(player_placements)
  print("Complete")
  return player_placements

def player_placement_format():
  """Format's player placement data aquired by get_placements(), currently outputs to a Placements.csv file"""
  print("Formatting placement data...")
  #Creating a new dictionary to properly format into a dataframe
  new_dict = {key: player_placements[value] for key, value in user_discrim_dict.items() if value in player_placements}
  column_headers = []
  #Grab the tournament names as column headers
  for tourney in tournies:
    tourney = tourney.split("/", 1)[1]
    column_headers.append(tourney)

  #Declair the dataframe and convert into csv file
  df = pd.DataFrame.from_dict(new_dict, orient='index', columns=column_headers)
  df.to_csv("Placements.csv", index=True)
  print("Complete")


    



def write_tourney_names_to_files(tournies):
  """Writes tourney names/slugs with dates to text files.
  Allows for a simple overview summary of tournaments.
  """
  i = 1
  with open('tourney_names.txt', 'w') as names, open('tourney_slugs.txt', 'w') as slugs:
    
    for tourney in tournies.values():
      notable_entries = ""
      if tourney.notable_entries:
        notable_entries = "--- " + ", ".join(tourney.notable_entries)
      url = 'https://start.gg/' + tourney.slug
      names.write(f'{tourney.name} --- {tourney.start_time.strftime("%m/%d")} --- {tourney.city}, {tourney.state} --- {url} {notable_entries}\n')
      slugs.write(f'{tourney.start_time} --- {tourney.slug} --- {tourney.city}, {tourney.state} {notable_entries}\n')
      
      i = i + 1


def write_removed_events_to_files(removed_events):
  """Writes tourney names/slugs with dates to text files.
  Allows for a simple overview summary of tournaments.
  """
  i = 1
  with open('removed_events.txt', 'w') as file:
    
    for event in removed_events:
      tourney = event.tourney
      file.write(f'{i}.) {tourney.start_time} --- Tourney: {tourney.name} --- Event: {event.name} --- {tourney.city}, {tourney.state}\n')
      
      i = i + 1


def collect_tournies_for_users():
  """Gathers a collection of tournaments and associated events for a user in a given season."""

  tourney_dict = dict()
  out_of_bounds_ctr = 0

  # Keywords that should help exclude non-viable events
  filter_names = {'squad strike', 'crew battle', 'redemption', 'ladder', 'doubles', 'amateur'}
  
  for user_id, player_name in user_dict.items():
    print("Processing " + player_name + "'s tournaments...")
    query_variables = {"userId": user_id}

    result = execute_query(queries.get_events_by_user, query_variables)
    res_data = json.loads(result)
    if 'errors' in res_data:
        print('Error:')
        print(res_data['errors'])

    for event_json in res_data['data']['user']['events']['nodes']:
      cut_off_date_start = datetime(2023, 1, 1)
      cut_off_date_end = datetime(2023, 3, 31)
      
      tourney = Tournament(event_json['tournament'])
      event = Event(event_json)
      event.tourney = tourney
      tourney.events.append(event)

      # Validate PR eligibility
      if tourney.is_online:
        removed_events.add(event)
        continue
      if event.num_entrants < 8:
        removed_events.add(event)
        continue
      if event.is_teams_event:
        removed_events.add(event)
        continue

      is_not_singles = 1 in [name in event.name.lower() for name in filter_names]
      if is_not_singles:
        removed_events.add(event)
        continue

      if tourney.start_time < cut_off_date_start or tourney.start_time > cut_off_date_end:
        # If three consecutive tournaments being processed is outside of the season's window,
        # we can feel confident that the remaining tournaments to process are also out of bounds
        out_of_bounds_ctr += 1
        if out_of_bounds_ctr == 3:
          break
        continue
      out_of_bounds_ctr = 0

      gamerTag = res_data['data']['user']['player']['gamerTag']

      event_dict[event.slug] = event

      # If tournament is out of state, keep track of who attended from Kentucky
      if tourney.state != "KY":
        if user_id in user_stats:
          user_stats[user_id].all_tournies.append(tourney)
        else:
          user = User()
          user.user_id = user_id
          user.all_tournies.append(tourney)
          user.gamer_tag = gamerTag
          user_stats[user_id] = user

        if tourney.slug in tourney_dict:
          tourney_dict[tourney.slug].notable_entries.append(gamerTag)
        else:
          tourney.notable_entries.append(gamerTag)
          tourney_dict[tourney.slug] = tourney
      else:
        tourney_dict[tourney.slug] = tourney

        if user_id in user_stats:
          user_stats[user_id].all_tournies.append(tourney)
          user_stats[user_id].ky_tournies.append(tourney)
        else:
          user = User()
          user.user_id = user_id
          user.all_tournies.append(tourney)
          user.ky_tournies.append(tourney)
          user.gamer_tag = gamerTag
          user_stats[user_id] = user
        
  return tourney_dict


def collect_tournies_for_users_last_season():
  """Gathers a collection of tournaments and associated events for a user in a given season."""

  tourney_dict = dict()
  out_of_bounds_ctr = 0
  
  for user_id, player_name in user_dict.items():
    print("Processing " + player_name + "'s tournaments...")
    query_variables = {"userId": user_id}

    result = execute_query(queries.get_tournies_by_user, query_variables)
    res_data = json.loads(result)
    if 'errors' in res_data:
        print('Error:')
        print(res_data['errors'])

    for tourney_json in res_data['data']['user']['tournaments']['nodes']:
      season_window_found = False
      cut_off_date_start = datetime(2023, 1, 1)
      cut_off_date_end = datetime(2023, 4, 3)
      
      tourney = Tournament(tourney_json)
      
      if tourney.is_online:
        continue

      if tourney.start_time < cut_off_date_start or tourney.start_time > cut_off_date_end:
        # If three consecutive tournaments being processed is outside of the season's window,
        # we can feel confident that the remaining tournaments to process are also out of bounds
        if season_window_found:
          out_of_bounds_ctr += 1
          if out_of_bounds_ctr == 3:
            break
          continue
      else: # Within season window
        season_window_found = True
        out_of_bounds_ctr = 0

        tourney_dict[tourney.slug] = tourney
        user_to_tournies[user_id] = tourney.slug

  return tourney_dict


def set_events(tournies):
  """Queries events per tournaments. Attempts to filter out non-Singles events.
  Adds results to collection.
  """
  for tourney_slug, tourney_obj in tournies.items():
    print(f'\n{tourney_obj.name}')
    query_variables = {"slug": tourney_slug}
    result = execute_query(queries.get_events_by_tournament, query_variables)
    res_data = json.loads(result)
    if 'errors' in res_data:
        print('Error:')
        print(res_data['errors'])

    for event_json in res_data['data']['tournament']['events']:
      event = Event(event_json)
      print(f'---{event.name}')

      # Filter out events that are most likely not Singles events
      if (is_event_eligible(event)):
         tournies[tourney_slug].events.append(event)
      else:
        remove_event(event, tourney_obj)
        continue
      
  print('#########################################') 
  temp_dict = tournies.copy()
  for tourney_slug, tourney_obj in temp_dict.items():
    if tourney_obj.events == []:
      print(f'Removing  {tourney_obj.name}\n')
      tournies.pop(tourney_slug)


def is_event_eligible(event):
  """Checks for various conditions that would make an Event ineligible for PR."""
  
  is_eligible = True

  filter_names = {'squad strike', 'crew battle', 'redemption', 'ladder', 'doubles', 'amateur'}
  is_not_singles = 1 in [name in event.name.lower() for name in filter_names]
  if is_not_singles:
    is_eligible = False
  
  if event.is_teams_event:
    is_eligible = False
  
  if event.num_entrants < 12 and event.start_time < datetime(2022, 11, 14):
    is_eligible = False
  
  if event.num_entrants < 8 and event.start_time >= datetime(2022, 11, 14):
    is_eligible = False
  
  if event.is_teams_event:
    is_eligible = False
  
  if event.activity_state == 'CREATED':
    is_eligible = False
  
  return is_eligible


def remove_event(event, tourney):
  """Removes event from collection."""

  print(f'Removing event:  {tourney.name} -- {event.name}')
  removed_events.add(event)


def write_user_stats_to_file(user_stats):
  """Writes user stats to file."""

  with open('user_stats.txt', 'w') as file:
    for user in user_stats.values():
      file.write(f'{user.gamer_tag} --- All tournies: {len(user.all_tournies)} --- KY events: {len(user.ky_tournies)}\n')


def init_clients():
  """Retrieves oauth tokens from a text file."""
  
  api_version = 'alpha'
  clients = []
  with open('tokens.txt', 'r') as file:
    for token in file:
      client = GraphQLClient('https://api.start.gg/gql/' + api_version)
      client.inject_token('Bearer ' + token.strip())
      clients.append(client)

  return clients
      

def write_tourney_info_to_google_sheet(tournies):
  """Writes tourney data to a specified Google Sheet's Worksheet."""

  gspread_client = gspread.service_account(filename='service_account.json')
  sh = gspread_client.open("Test Sheet")
  ws = sh.worksheet("ayo")
  
  row_num = 1
  rows = []

  for tourney in tournies.values():
    row = []
    row.append(str(row_num))
    row.append(tourney.name)
    row.append(tourney.start_time.strftime("%m/%d"))
    entrants = get_entrants(tourney)
    row.append(entrants)
    row = add_blank_fields_to_row(row, 16)
    row.append(", ".join(tourney.notable_entries))
    row.append(f'https://start.gg/{tourney.slug}/details')

    rows.append(row)

    row_num += 1

  ws.update('A1', rows)


def add_blank_fields_to_row(row, num_fields):
  """Adds the provided number of empty fields to a row."""

  for i in range(num_fields):
    row.append('')
  
  return row


def get_entrants(tourney):
  '''Fetches the likely number of entrants for a Singles event. 
  Takes the highest entrant count from all the (filtered) entrants a tourney has.
  If a tournament has multiple eligible events, this function will most likely return incorrect results.
  '''
  entrants = 0
  for event in tourney.events:
    if event.num_entrants > entrants:
      entrants = event.num_entrants
  
  if entrants == 0:
    entrants = 'Error'
  else:
    entrants = str(entrants)

  return entrants


client_idx = 0
current_token_index = 0
request_count = 0
clients = init_clients()
ultimate_id = '1386'

request_threshold = 79

##### Collections #####
user_to_tournies = dict()   #user_id -> tourney_slug
user_to_events = dict()     #user_id -> event_slug
user_to_gamer_tag = dict()  #user_id -> gamer_tag
user_dict = dict()          #user_id -> User object
user_stats = dict()

tourney_to_events = dict()  #tourney_slug -> event slug
event_dict = dict()         #event_slug -> Event object
removed_events = set()      #event_slug
removed_tournies = set()    #tourney_slug
##### End Collections #####

user_discrim_dict = dict()  #Collects the discriminator of players


collect_user_ids_from_file()
# # Sort results chronologically from earliest in the season to latest
tournies = dict(sorted(collect_tournies_for_users_last_season().items(), key=lambda kvp: kvp[1].start_time))

set_events(tournies)
write_tourney_names_to_files(tournies)
write_user_stats_to_file(user_stats)
# write_tourney_info_to_google_sheet(tournies)
# write_removed_events_to_files(removed_events)
player_placements = get_placements(tournies)
player_placement_format()

# TODO: Add all_events_removed_from_tourney idea
print('Process is complete.')