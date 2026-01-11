import json
import os
from datetime import datetime
from typing import *

import httpx

from ghunt import globals as gb
from ghunt.objects.base import *
from ghunt.helpers.utils import *
from ghunt.objects.utils import *
from ghunt.helpers.knowledge import get_gmaps_type_translation


async def get_reviews(as_client: httpx.AsyncClient, gaia_id: str) -> Tuple[str, Dict[str, int], List[MapsReview], List[MapsPhoto]]:
    """Extracts reviews location data from data[45][0]"""
    reviews_pb = f'!1s{gaia_id}!2m3!1s!7e81!15i20393!6m2!4b1!7b1!9m0!10m5!1b1!5b1!9m1!1e3!11b1!14m60!1m48!1m4!1m3!1e3!1e2!1e4!3m5!2m4!3m3!1m2!1i260!2i365!4m1!3i10!10b1!11m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e8!2b0!3e3!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!2m5!1e1!1e4!1e3!1e5!1e2!3b0!4b1!5m1!1e1!7b1!17m0!18m15!1m3!1d7264635!2d75.82!3d20.71!2m3!1f0!2f0!3f0!3m2!1i675!2i730!4f13.1!6m2!1f0!2f0!41m15!1i10!2m9!2b1!3b1!5b1!7b1!12m4!1b1!2b1!4m1!1e1!3s!7m2!1m1!1e1'
    url = f'https://www.google.com/locationhistory/preview/mas?authuser=0&hl=en&gl=in&pb={reviews_pb}'
    
    req = await as_client.get(url)
    if req.status_code == 302 and req.headers.get("Location", "").startswith("https://www.google.com/sorry/index"):
        return "failed", {}, [], []
    
    data = json.loads(req.text[5:])
    
    # Check if we have review data at data[45][0]
    if len(data) <= 45 or not data[45] or not isinstance(data[45], list) or len(data[45]) == 0:
        return "empty", {}, [], []
    
    if not isinstance(data[45][0], list) or len(data[45][0]) == 0:
        return "empty", {}, [], []
    
    reviews_data = data[45][0]
    agg_reviews = []
    stats = {}
    
    # Parse reviews from data[45][0]
    for review_item in reviews_data:
        try:
            if not isinstance(review_item, list) or len(review_item) < 5:
                continue
            
            review = MapsReview()
            
            # Extract review ID from review_item[1][0]
            if len(review_item) > 1 and isinstance(review_item[1], list) and len(review_item[1]) > 0:
                review_id_val = review_item[1][0]
                if isinstance(review_id_val, str):
                    review.id = review_id_val
                elif isinstance(review_id_val, list) and len(review_id_val) > 0:
                    review.id = str(review_id_val[0]) if review_id_val[0] else ""
                else:
                    review.id = str(review_id_val) if review_id_val else ""
            
            # Extract date from review_item[3][1] or review_item[3][2] (timestamp in microseconds)
            if len(review_item) > 3 and isinstance(review_item[3], list):
                timestamp_us = None
                # Try index [1] first
                if len(review_item[3]) > 1 and review_item[3][1]:
                    timestamp_us = review_item[3][1]
                # Fallback to index [2] if [1] is not available
                elif len(review_item[3]) > 2 and review_item[3][2]:
                    timestamp_us = review_item[3][2]
                
                if timestamp_us:
                    # Convert from microseconds to seconds
                    review.date = datetime.utcfromtimestamp(timestamp_us / 1000000)
            
            # Extract place data from review_item[4]
            if len(review_item) > 4 and isinstance(review_item[4], list):
                place_data = review_item[4]
                
                # Place ID from place_data[14]
                if len(place_data) > 14 and place_data[14]:
                    if isinstance(place_data[14], list) and len(place_data[14]) > 0:
                        review.location.id = place_data[14][0]
                    else:
                        review.location.id = str(place_data[14])
                
                # Place name from place_data[2]
                if len(place_data) > 2 and place_data[2]:
                    review.location.name = place_data[2]
                
                # Place address from place_data[3]
                if len(place_data) > 3 and place_data[3]:
                    review.location.address = place_data[3]
                
                # Coordinates from place_data[0][2], place_data[0][3]
                if len(place_data) > 0 and isinstance(place_data[0], list) and len(place_data[0]) > 3:
                    review.location.position.latitude = place_data[0][2]
                    review.location.position.longitude = place_data[0][3]
            
            agg_reviews.append(review)
            
        except (IndexError, TypeError, KeyError, ValueError) as e:
            continue
    
    # Save review locations to a file for later reading
    if agg_reviews:
        locations_data = []
        for review in agg_reviews:
            if review.location.position.latitude and review.location.position.longitude:
                locations_data.append({
                    "review_id": review.id,
                    "date": review.date.isoformat() if review.date else None,
                    "location": {
                        "id": review.location.id,
                        "name": review.location.name,
                        "address": review.location.address,
                        "latitude": review.location.position.latitude,
                        "longitude": review.location.position.longitude
                    }
                })
        
        if locations_data:
            # Save review locations to a file in the current working directory
            output_file = f"review_locations_{gaia_id}.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(locations_data, f, indent=2, ensure_ascii=False)
    
    return "", stats, agg_reviews, []

def output(err: str, stats: Dict[str, int], reviews: List[MapsReview], photos: List[MapsPhoto], gaia_id: str):
    """Pretty print the Maps results, and do some guesses."""

    print(f"\nProfile page : https://www.google.com/maps/contrib/{gaia_id}/reviews")

    if err == "failed":
        print("\n[-] Your IP has been blocked by Google. Try again later.")

    reviews_and_photos: List[MapsReview|MapsPhoto] = reviews + photos
    if err != "private" and (err == "empty" or not reviews_and_photos):
        print("\n[-] No review.")
        return

    print("\n[Statistics]")
    for section, number in stats.items():
        if number:
            print(f"{section} : {number}")

    if err == "private":
        print("\n[-] Reviews are private.")
        return

    print("\n[Reviews]")
    if reviews:
        avg_ratings = round(sum([x.rating for x in reviews if x.rating]) / len([x for x in reviews if x.rating]), 1) if any(x.rating for x in reviews) else 0
        print(f"[+] Average rating : {ppnb(avg_ratings)}/5\n")
    else:
        print("[-] No reviews to analyze.\n")
        return

    # I removed the costs calculation because of a Google update : https://github.com/mxrch/GHunt/issues/529

    # costs_table = {
    #     1: "Inexpensive",
    #     2: "Moderately expensive",
    #     3: "Expensive",
    #     4: "Very expensive"
    # }

    # total_costs = 0
    # costs_stats = {x:0 for x in range(1,5)}
    # for review in reviews_and_photos:
    #     if review.location.cost_level:
    #         costs_stats[review.location.cost_level] += 1
    #         total_costs += 1
    # costs_stats = dict(sorted(costs_stats.items(), key=lambda item: item[1], reverse=True)) # We sort the dict by cost popularity

    # if total_costs:
    #     print("[Costs]")
    #     for cost, desc in costs_table.items():
    #         line = f"> {ppnb(round(costs_stats[cost]/total_costs*100, 1))}% {desc} ({costs_stats[cost]})"
    #         style = ""
    #         if not costs_stats[cost]:
    #             style = "bright_black"
    #         elif costs_stats[cost] == list(costs_stats.values())[0]:
    #             style = "spring_green1"
    #         gb.rc.print(line, style=style)
            
    #     avg_costs = round(sum([x*y for x,y in costs_stats.items()]) / total_costs)
    #     print(f"\n[+] Average costs : {costs_table[avg_costs]}")
    # else:
    #     print("[-] No costs data.")

    types = {}
    for review in reviews_and_photos:
        for type in review.location.types:
            if type not in types:
                types[type] = 0
            types[type] += 1
    types = dict(sorted(types.items(), key=lambda item: item[1], reverse=True))

    types_and_tags = {}
    for review in reviews_and_photos:
        for type in review.location.types:
            if type not in types_and_tags:
                types_and_tags[type] = {}
            for tag in review.location.tags:
                if tag not in types_and_tags[type]:
                    types_and_tags[type][tag] = 0
                types_and_tags[type][tag] += 1
            types_and_tags[type] = dict(sorted(types_and_tags[type].items(), key=lambda item: item[1], reverse=True))
    types_and_tags = dict(sorted(types_and_tags.items()))

    if types_and_tags:
        print("\nTarget's locations preferences :")

        unknown_trads = []
        for type, type_count in types.items():
            tags_counts = types_and_tags[type]
            translation = get_gmaps_type_translation(type)
            if not translation:
                unknown_trads.append(type)
            gb.rc.print(f"\n🏨 [underline]{translation if translation else type.title()} [{type_count}]", style="bold")
            nb = 0
            for tag, tag_count in list(tags_counts.items()):
                if nb >= 7:
                    break
                elif tag.lower() == type:
                    continue
                print(f"- {tag} ({tag_count})")
                nb += 1

        if unknown_trads:
            print(f"\n⚠️ The following gmaps types haven't been found in GHunt\'s knowledge.")
            for type in unknown_trads:
                print(f"- {type}")
            print("Please open an issue on the GHunt Github or submit a PR to add it !")

    # Check if review locations file was saved
    locations_file = f"review_locations_{gaia_id}.json"
    if os.path.exists(locations_file):
        gb.rc.print(f"\n[+] Review locations saved to {locations_file} !", style="italic")
 