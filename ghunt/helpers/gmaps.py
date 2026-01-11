from dateutil.relativedelta import relativedelta
from datetime import datetime
import json
from geopy import distance
from geopy.geocoders import Nominatim
from typing import *

import httpx
from alive_progress import alive_bar

from ghunt import globals as gb
from ghunt.objects.base import *
from ghunt.helpers.utils import *
from ghunt.objects.utils import *
from ghunt.helpers.knowledge import get_gmaps_type_translation


def get_datetime(datepublished: str):
    """
        Get an approximative date from the maps review date
        Examples : 'last 2 days', 'an hour ago', '3 years ago'
    """
    if datepublished.split()[0] in ["a", "an"]:
        nb = 1
    else:
        if datepublished.startswith("last"):
            nb = int(datepublished.split()[1])
        else:
            nb = int(datepublished.split()[0])

    if "minute" in datepublished:
        delta = relativedelta(minutes=nb)
    elif "hour" in datepublished:
        delta = relativedelta(hours=nb)
    elif "day" in datepublished:
        delta = relativedelta(days=nb)
    elif "week" in datepublished:
        delta = relativedelta(weeks=nb)
    elif "month" in datepublished:
        delta = relativedelta(months=nb)
    elif "year" in datepublished:
        delta = relativedelta(years=nb)
    else:
        delta = relativedelta()

    return (datetime.today() - delta).replace(microsecond=0, second=0)

async def get_reviews(as_client: httpx.AsyncClient, gaia_id: str) -> Tuple[str, Dict[str, int], List[MapsReview], List[MapsPhoto]]:
    """Extracts the target's statistics, reviews and photos."""
    next_page_token = ""
    agg_reviews = []
    agg_photos = []
    stats = {}

    req = await as_client.get(f"https://www.google.com/locationhistory/preview/mas?authuser=0&hl=en&gl=us&pb={gb.config.templates['gmaps_pb']['stats'].format(gaia_id)}")
    if req.status_code == 302 and req.headers["Location"].startswith("https://www.google.com/sorry/index"):
        return "failed", stats, [], []

    data = json.loads(req.text[5:])
    
    # Parse stats with safeguards
    if len(data) <= 16 or not data[16] or len(data[16]) <= 8 or not data[16][8]:
        return "empty", stats, [], []
    
    if not isinstance(data[16][8], list) or len(data[16][8]) == 0 or not isinstance(data[16][8][0], list):
        return "empty", stats, [], []
    
    stats = {}
    for sec in data[16][8][0]:
        if isinstance(sec, list) and len(sec) > 7:
            stats[sec[6]] = sec[7]
    
    total_reviews = stats.get("Reviews", 0) + stats.get("Ratings", 0) + stats.get("Photos", 0)
    if not total_reviews:
        return "empty", stats, [], []

    with alive_bar(total_reviews, receipt=False) as bar:
        for category in ["reviews", "photos"]:
            first = True
            while True:
                if first:
                    req = await as_client.get(f"https://www.google.com/locationhistory/preview/mas?authuser=0&hl=en&gl=us&pb={gb.config.templates['gmaps_pb'][category]['first'].format(gaia_id)}")
                    first = False
                else:
                    req = await as_client.get(f"https://www.google.com/locationhistory/preview/mas?authuser=0&hl=en&gl=us&pb={gb.config.templates['gmaps_pb'][category]['page'].format(gaia_id, next_page_token)}")
                data = json.loads(req.text[5:])

                new_reviews = []
                new_photos = []
                next_page_token = ""

                # Reviews
                if category == "reviews":
                    reviews_data = None
                    reviews_format = None
                    
                    # Try new format first: data[22][0] or data[45][0]
                    if len(data) > 22 and data[22] and isinstance(data[22], list) and len(data[22]) > 0:
                        if isinstance(data[22][0], list) and len(data[22][0]) > 0:
                            # Check if first element looks like new format card structure
                            first_item = data[22][0][0]
                            if isinstance(first_item, list) and len(first_item) >= 2:
                                reviews_data = data[22][0]
                                reviews_format = "new"

                    # Try data[45][0] (authenticated format)
                    if not reviews_data and len(data) > 45 and data[45] and isinstance(data[45], list) and len(data[45]) > 0:
                        if isinstance(data[45][0], list) and len(data[45][0]) > 0:
                            first_item = data[45][0][0]
                            if isinstance(first_item, list) and len(first_item) >= 2:
                                reviews_data = data[45][0]
                                reviews_format = "new"

                    # Fall back to old format: data[24][0]
                    if not reviews_data and len(data) > 24 and data[24] and isinstance(data[24], list) and len(data[24]) > 0:
                        if isinstance(data[24][0], list):
                            reviews_data = data[24][0]
                            reviews_format = "old"

                    # Print format being used (before checking if data exists)
                    if reviews_format == "new":
                        print(f"[+] Using NEW format for reviews (data[{22 if len(data) > 22 and data[22] else 45}][0])")
                    else:
                        print(f"[+] Using OLD format for reviews (data[24][0])")

                    if not reviews_data:
                        return "private", stats, [], []

                    if not reviews_data:
                        break
                    
                    for review_item in reviews_data:
                        try:
                            review = MapsReview()
                            
                            if reviews_format == "new":
                                # New format: [place_wrapper, review_wrapper, null, location_summary]
                                if not isinstance(review_item, list) or len(review_item) < 2:
                                    continue
                                
                                place_wrapper = review_item[0] if len(review_item) > 0 else None
                                review_wrapper = review_item[1] if len(review_item) > 1 else None
                                
                                if not place_wrapper or not isinstance(place_wrapper, list) or len(place_wrapper) < 2:
                                    continue
                                if not review_wrapper or not isinstance(review_wrapper, list):
                                    continue
                                
                                place_info = place_wrapper[1] if len(place_wrapper) > 1 and isinstance(place_wrapper[1], list) else None
                                
                                # Extract review info
                                if len(review_wrapper) > 0:
                                    review.id = review_wrapper[0] if review_wrapper[0] else ""
                                
                                if len(review_wrapper) > 1 and isinstance(review_wrapper[1], list) and len(review_wrapper[1]) > 2:
                                    timestamp_us = review_wrapper[1][2]
                                    if timestamp_us:
                                        review.date = datetime.utcfromtimestamp(timestamp_us / 1000000)
                                
                                if len(review_wrapper) > 2 and isinstance(review_wrapper[2], list):
                                    rating_comment = review_wrapper[2]
                                    if len(rating_comment) > 0:
                                        if isinstance(rating_comment[0], list) and len(rating_comment[0]) > 0:
                                            review.rating = rating_comment[0][0]
                                        elif isinstance(rating_comment[0], (int, float)):
                                            review.rating = rating_comment[0]
                                    
                                    if len(rating_comment) > 15 and rating_comment[15]:
                                        try:
                                            if isinstance(rating_comment[15], list) and len(rating_comment[15]) > 0:
                                                if isinstance(rating_comment[15][0], list) and len(rating_comment[15][0]) > 0:
                                                    review.comment = rating_comment[15][0][0]
                                        except (IndexError, TypeError):
                                            pass
                                
                                # Extract place info
                                if place_info and isinstance(place_info, list):
                                    if len(place_info) > 14 and place_info[14]:
                                        if isinstance(place_info[14], list) and len(place_info[14]) > 0:
                                            review.location.id = place_info[14][0]
                                        else:
                                            review.location.id = str(place_info[14])
                                    elif len(place_info) > 1:
                                        review.location.id = str(place_info[1]) if place_info[1] else ""
                                    
                                    if len(place_info) > 4:
                                        review.location.name = place_info[4] if place_info[4] else ""
                                    
                                    if len(place_info) > 5:
                                        review.location.address = place_info[5] if place_info[5] else ""
                                    
                                    if len(place_info) > 3 and isinstance(place_info[3], list) and len(place_info[3]) >= 4:
                                        review.location.position.latitude = place_info[3][2]
                                        review.location.position.longitude = place_info[3][3]
                                    
                                    if len(place_info) > 9 and place_info[9]:
                                        types = []
                                        tags = []
                                        for type_entry in place_info[9]:
                                            if isinstance(type_entry, list) and len(type_entry) > 1:
                                                types.append(type_entry[1])
                                                if len(type_entry) > 0:
                                                    tags.append(type_entry[0])
                                            elif isinstance(type_entry, str):
                                                types.append(type_entry)
                                        review.location.types = types
                                        review.location.tags = tags
                            
                            else:
                                # Old format
                                if not isinstance(review_item, list) or len(review_item) < 7:
                                    continue
                                
                                if len(review_item[6]) > 0:
                                    review.id = review_item[6][0]
                                
                                if len(review_item[6]) > 1 and isinstance(review_item[6][1], list) and len(review_item[6][1]) > 3:
                                    review.date = datetime.utcfromtimestamp(review_item[6][1][3] / 1000000)
                                
                                if len(review_item[6]) > 2 and isinstance(review_item[6][2], list):
                                    if len(review_item[6][2]) > 0 and isinstance(review_item[6][2][0], list) and len(review_item[6][2][0]) > 0:
                                        review.rating = review_item[6][2][0][0]
                                    
                                    if len(review_item[6][2]) > 15 and review_item[6][2][15]:
                                        if isinstance(review_item[6][2][15], list) and len(review_item[6][2][15]) > 0:
                                            if isinstance(review_item[6][2][15][0], list) and len(review_item[6][2][15][0]) > 0:
                                                review.comment = review_item[6][2][15][0][0]
                                
                                if len(review_item) > 1 and isinstance(review_item[1], list):
                                    if len(review_item[1]) > 14 and review_item[1][14]:
                                        if isinstance(review_item[1][14], list) and len(review_item[1][14]) > 0:
                                            review.location.id = review_item[1][14][0]
                                    
                                    if len(review_item[1]) > 2:
                                        review.location.name = review_item[1][2] if review_item[1][2] else ""
                                    
                                    if len(review_item[1]) > 3:
                                        review.location.address = review_item[1][3] if review_item[1][3] else ""
                                    
                                    if len(review_item[1]) > 4:
                                        review.location.tags = review_item[1][4] if review_item[1][4] else []
                                    
                                    if len(review_item[1]) > 8 and review_item[1][8]:
                                        review.location.types = [x for x in review_item[1][8] if x]
                                    
                                    if len(review_item[1]) > 0 and review_item[1][0]:
                                        if isinstance(review_item[1][0], list) and len(review_item[1][0]) > 3:
                                            review.location.position.latitude = review_item[1][0][2]
                                            review.location.position.longitude = review_item[1][0][3]
                            
                            new_reviews.append(review)
                            bar()
                            
                        except (IndexError, TypeError, KeyError, ValueError) as e:
                            # Skip malformed review entries
                            continue

                    agg_reviews += new_reviews

                    # Check for next page token
                    if not new_reviews:
                        break
                    
                    if reviews_format == "new":
                        # New format pagination (if exists)
                        if len(data) > 22 and data[22] and isinstance(data[22], list) and len(data[22]) > 3 and data[22][3]:
                            next_page_token = data[22][3].strip("=")
                        elif len(data) > 45 and data[45] and isinstance(data[45], list) and len(data[45]) > 3 and data[45][3]:
                            next_page_token = data[45][3].strip("=")
                        else:
                            break
                    else:
                        # Old format pagination
                        if len(data) > 24 and data[24] and isinstance(data[24], list) and len(data[24]) > 3 and data[24][3]:
                            next_page_token = data[24][3].strip("=")
                        else:
                            break

                # Photos
                elif category == "photos":
                    photos_data = None
                    photos_format = None
                    
                    # Try new format first: data[22][0]
                    if len(data) > 22 and data[22] and isinstance(data[22], list) and len(data[22]) > 0:
                        if isinstance(data[22][0], list) and len(data[22][0]) > 0:
                            # Check if first element looks like new format card structure
                            first_item = data[22][0][0]
                            if isinstance(first_item, list) and len(first_item) >= 2:
                                photos_data = data[22][0]
                                photos_format = "new"

                    # Fall back to old format: data[22][1]
                    if not photos_data and len(data) > 22 and data[22] and isinstance(data[22], list) and len(data[22]) > 1:
                        if isinstance(data[22][1], list):
                            photos_data = data[22][1]
                            photos_format = "old"

                    # Print format being used (before checking if data exists)
                    if photos_format == "new":
                        print(f"[+] Using NEW format for photos (data[22][0])")
                    else:
                        print(f"[+] Using OLD format for photos (data[22][1])")

                    if not photos_data:
                        return "private", stats, [], []

                    if not photos_data:
                        break
                    
                    for photo_item in photos_data:
                        try:
                            photos = MapsPhoto()
                            
                            if photos_format == "new":
                                # New format: [place_wrapper, photo_wrapper, null, location_summary]
                                if not isinstance(photo_item, list) or len(photo_item) < 2:
                                    continue
                                
                                place_wrapper = photo_item[0] if len(photo_item) > 0 else None
                                photo_wrapper = photo_item[1] if len(photo_item) > 1 else None
                                
                                if not place_wrapper or not isinstance(place_wrapper, list) or len(place_wrapper) < 2:
                                    continue
                                if not photo_wrapper or not isinstance(photo_wrapper, list):
                                    continue
                                
                                place_info = place_wrapper[1] if len(place_wrapper) > 1 and isinstance(place_wrapper[1], list) else None
                                
                                # Extract photo info from photo_wrapper
                                # Note: Photo structure in new format may differ, need to adapt based on actual response
                                # For now, try to extract what we can
                                if len(photo_wrapper) > 0:
                                    photos.id = str(photo_wrapper[0]) if photo_wrapper[0] else ""
                                
                                # Extract place info (same as reviews)
                                if place_info and isinstance(place_info, list):
                                    if len(place_info) > 14 and place_info[14]:
                                        if isinstance(place_info[14], list) and len(place_info[14]) > 0:
                                            photos.location.id = place_info[14][0]
                                        else:
                                            photos.location.id = str(place_info[14])
                                    
                                    if len(place_info) > 4:
                                        photos.location.name = place_info[4] if place_info[4] else ""
                                    
                                    if len(place_info) > 5:
                                        photos.location.address = place_info[5] if place_info[5] else ""
                                    
                                    if len(place_info) > 3 and isinstance(place_info[3], list) and len(place_info[3]) >= 4:
                                        photos.location.position.latitude = place_info[3][2]
                                        photos.location.position.longitude = place_info[3][3]
                                    
                                    if len(place_info) > 9 and place_info[9]:
                                        types = []
                                        tags = []
                                        for type_entry in place_info[9]:
                                            if isinstance(type_entry, list) and len(type_entry) > 1:
                                                types.append(type_entry[1])
                                                if len(type_entry) > 0:
                                                    tags.append(type_entry[0])
                                            elif isinstance(type_entry, str):
                                                types.append(type_entry)
                                        photos.location.types = types
                                        photos.location.tags = tags
                                
                                # Photo URL and date extraction for new format
                                # This may need adjustment based on actual new format structure
                                # For now, set defaults
                                photos.url = ""
                                photos.date = datetime.utcnow()
                            
                            else:
                                # Old format
                                if not isinstance(photo_item, list) or len(photo_item) < 1:
                                    continue
                                
                                if not isinstance(photo_item[0], list) or len(photo_item[0]) < 11:
                                    continue
                                
                                if len(photo_item[0]) > 10:
                                    photos.id = photo_item[0][10]
                                
                                if len(photo_item[0]) > 6 and isinstance(photo_item[0][6], list) and len(photo_item[0][6]) > 0:
                                    photos.url = photo_item[0][6][0].split("=")[0] if photo_item[0][6][0] else ""
                                
                                if len(photo_item[0]) > 21 and isinstance(photo_item[0][21], list):
                                    if len(photo_item[0][21]) > 6 and isinstance(photo_item[0][21][6], list):
                                        if len(photo_item[0][21][6]) > 8 and isinstance(photo_item[0][21][6][8], list) and len(photo_item[0][21][6][8]) >= 4:
                                            date = photo_item[0][21][6][8]
                                            photos.date = datetime(date[0], date[1], date[2], date[3])  # UTC
                                
                                if len(photo_item) > 1 and isinstance(photo_item[1], list):
                                    if len(photo_item[1]) > 14 and photo_item[1][14]:
                                        if isinstance(photo_item[1][14], list) and len(photo_item[1][14]) > 0:
                                            photos.location.id = photo_item[1][14][0]
                                    
                                    if len(photo_item[1]) > 2:
                                        photos.location.name = photo_item[1][2] if photo_item[1][2] else ""
                                    
                                    if len(photo_item[1]) > 3:
                                        photos.location.address = photo_item[1][3] if photo_item[1][3] else ""
                                    
                                    if len(photo_item[1]) > 4:
                                        photos.location.tags = photo_item[1][4] if photo_item[1][4] else []
                                    
                                    if len(photo_item[1]) > 8 and photo_item[1][8]:
                                        photos.location.types = [x for x in photo_item[1][8] if x]
                                    
                                    if len(photo_item[1]) > 0 and photo_item[1][0]:
                                        if isinstance(photo_item[1][0], list) and len(photo_item[1][0]) > 3:
                                            photos.location.position.latitude = photo_item[1][0][2]
                                            photos.location.position.longitude = photo_item[1][0][3]
                                    
                                    if len(photo_item[1]) > 31 and photo_item[1][31]:
                                        photos.location.cost_level = len(photo_item[1][31])
                            
                            new_photos.append(photos)
                            bar()
                            
                        except (IndexError, TypeError, KeyError, ValueError) as e:
                            # Skip malformed photo entries
                            continue

                    agg_photos += new_photos

                    # Check for next page token
                    if not new_photos:
                        break
                    
                    if photos_format == "new":
                        # New format pagination (if exists)
                        if len(data) > 22 and data[22] and isinstance(data[22], list) and len(data[22]) > 3 and data[22][3]:
                            next_page_token = data[22][3].strip("=")
                        else:
                            break
                    else:
                        # Old format pagination
                        if len(data) > 22 and data[22] and isinstance(data[22], list) and len(data[22]) > 3 and data[22][3]:
                            next_page_token = data[22][3].strip("=")
                        else:
                            break

    return "", stats, agg_reviews, agg_photos

def avg_location(locs: Tuple[float, float]):
    """
        Calculates the average location
        from a list of (latitude, longitude) tuples.
    """
    latitude = []
    longitude = []
    for loc in locs:
        latitude.append(loc[0])
        longitude.append(loc[1])

    latitude = sum(latitude) / len(latitude)
    longitude = sum(longitude) / len(longitude)
    return latitude, longitude

def translate_confidence(percents: int):
    """Translates the percents number to a more human-friendly text"""
    if percents >= 100:
        return "Extremely high"
    elif percents >= 80:
        return "Very high"
    elif percents >= 60:
        return "Little high"
    elif percents >= 40:
        return "Okay"
    elif percents >= 20:
        return "Low"
    elif percents >= 10:
        return "Very low"
    else:
        return "Extremely low"

def sanitize_location(location: Dict[str, str]):
    """Returns the nearest place from a Nomatim location response."""
    not_country = False
    not_town = False
    town = "?"
    country = "?"
    if "city" in location:
        town = location["city"]
    elif "village" in location:
        town = location["village"]
    elif "town" in location:
        town = location["town"]
    elif "municipality" in location:
        town = location["municipality"]
    else:
        not_town = True
    if not "country" in location:
        not_country = True
        location["country"] = country
    if not_country and not_town:
        return False
    location["town"] = town
    return location

def calculate_probable_location(geolocator: Nominatim, reviews_and_photos: List[MapsReview|MapsPhoto], gmaps_radius: int):
    """Calculates the probable location from a list of reviews and the max radius."""
    tmprinter = TMPrinter()
    radius = gmaps_radius

    locations = {}
    tmprinter.out(f"Calculation of the distance of each review...")
    for nb, review in enumerate(reviews_and_photos):
        if not review.location.position.latitude or not review.location.position.longitude:
            continue
        if review.location.id not in locations:
            locations[review.location.id] = {"dates": [], "locations": [], "range": None, "score": 0}
        location = (review.location.position.latitude, review.location.position.longitude)
        for review2 in reviews_and_photos:
            location2 = (review2.location.position.latitude, review2.location.position.longitude)
            dis = distance.distance(location, location2).km

            if dis <= radius:
                locations[review.location.id]["dates"].append(review2.date)
                locations[review.location.id]["locations"].append(location2)

        maxdate = max(locations[review.location.id]["dates"])
        mindate = min(locations[review.location.id]["dates"])
        locations[review.location.id]["range"] = maxdate - mindate
        tmprinter.out(f"Calculation of the distance of each review ({nb}/{len(reviews_and_photos)})...")

    tmprinter.clear()

    locations = {k: v for k, v in
                 sorted(locations.items(), key=lambda k: len(k[1]["locations"]), reverse=True)}  # We sort it

    tmprinter.out("Identification of redundant areas...")
    to_del = []
    for id in locations:
        if id in to_del:
            continue
        for id2 in locations:
            if id2 in to_del or id == id2:
                continue
            if all([loc in locations[id]["locations"] for loc in locations[id2]["locations"]]):
                to_del.append(id2)
    for hash in to_del:
        del locations[hash]

    tmprinter.out("Calculating confidence...")

    maxrange = max([locations[hash]["range"] for hash in locations])
    maxlen = max([len(locations[hash]["locations"]) for hash in locations])
    minreq = 3
    mingroups = 3

    score_steps = 4
    for hash, loc in locations.items():
        if len(loc["locations"]) == maxlen:
            locations[hash]["score"] += score_steps * 4
        if loc["range"] == maxrange:
            locations[hash]["score"] += score_steps * 3
        if len(locations) >= mingroups:
            others = sum([len(locations[h]["locations"]) for h in locations if h != hash])
            if len(loc["locations"]) > others:
                locations[hash]["score"] += score_steps * 2
        if len(loc["locations"]) >= minreq:
            locations[hash]["score"] += score_steps

    panels = sorted(set([loc["score"] for loc in locations.values()]), reverse=True)

    maxscore = sum([p * score_steps for p in range(1, score_steps + 1)])
    for panel in panels:
        locs = [loc for loc in locations.values() if loc["score"] == panel]
        if len(locs[0]["locations"]) == 1:
            panel /= 2
        if len(reviews_and_photos) < 4:
            panel /= 2
        confidence = translate_confidence(panel / maxscore * 100)
        for nb, loc in enumerate(locs):
            avg = avg_location(loc["locations"])
            while True:
                try:
                    location = geolocator.reverse(f"{avg[0]}, {avg[1]}", timeout=10).raw["address"]
                    break
                except:
                    pass
            location = sanitize_location(location)
            locs[nb]["avg"] = location
            del locs[nb]["locations"]
            del locs[nb]["score"]
            del locs[nb]["range"]
            del locs[nb]["dates"]

        tmprinter.clear()

        return confidence, locs

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

    geolocator = Nominatim(user_agent="nominatim")

    confidence, locations = calculate_probable_location(geolocator, reviews_and_photos, gb.config.gmaps_radius)
    if locations:
        print(f"\n[+] Probable location (confidence => {confidence}) :")

        loc_names = []
        for loc in locations:
            if loc.get('avg'):
                loc_names.append(
                    f"- {loc['avg'].get('town', '?')}, {loc['avg'].get('country', '?')}"
                )

        loc_names = set(loc_names)  # delete duplicates
        for loc in loc_names:
            print(loc)
    else:
        print("\n[-] Could not determine probable location.")