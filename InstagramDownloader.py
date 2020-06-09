import os
import time
import json
import pickle
import threading
import random
import re

import requests
from moviepy.video.io.ffmpeg_tools import ffmpeg_extract_subclip

from Delay import Delay
import Language
from MongoStorage import Storage, APIStorage

from pathlib import Path

import logging
logging.basicConfig(
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S')

from logging.handlers import TimedRotatingFileHandler
logname = "insta.log"
handler = TimedRotatingFileHandler(logname, when="midnight", interval=1)
handler.suffix = "%Y%m%d"
formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
handler.setFormatter(formatter)
logging.getLogger().addHandler(handler)

import pickle

class Uploader(object):
    def __init__(self, API, config, delay, number, storage, promote_message):
        self.api = API
        self.cfg = config
        self.delay = delay
        self.number = number
        self.storage = storage
        self.upload_worker = threading.Thread(target=self.upload_worker_func)
        self.running = False
        self.queue = []

        self.sleep = [0,60]

        self.counter = 0
        self.errors = 0
        self.PROMOTE_MESSAGE = promote_message

    def start(self):
        self.running = True
        self.upload_worker.start()

    def stop(self):
        self.running = False


    def extract_priority(self, json):
        if "priority" in json:
            return int(json["priority"])
        return 0

    def queue_contains(self, itemid):
        for item in self.queue:
            if item["item_id"] == itemid:
                return True
        return False

    def queue_contains_post(self, media_id, username):
        for item in self.queue:
            if item["username"] == username:
                if "media_id" in item and item["media_id"] == media_id:
                    return True
        return False


    def reload_api(self):
        self.api = self.storage.load()
        logging.info("Reloaded uploader #{}".format(self.storage.session_id))


    def send_media(self, url, itemid, mediatype, media_id, userid, username, download_from, sent, cut=False):
        user = self.cfg.get_user(userid)
        
        item = {"priority": user["priority"],
                "url": url,
                "item_id": itemid,
                "media_type": mediatype,
                "media_id": media_id,
                "cut": cut,
                "sent": sent,
                "userid": userid,
                "username": username,
                "download_from": download_from}

        self.queue.append(item)

    # filetype: photo (1) = .jpg, video (2) = .mp4
    def upload_file(self, item, filename, itemcode):
        itemtype = "video" if itemcode == 2 else "photo"
        filetype = "mp4" if itemcode == 2 else "jpg"
        full_path = str(Path("./videos/{f}.{t}".format(f=filename, t=filetype)))
        item_file = requests.get(item["url"])
        open(full_path, "wb").write(item_file.content)

        # if video length exceeds 60 seconds
        if "cut" in item and item["cut"]:
            new_path = str(Path("./videos/{f}_cut.mp4".format(f=filename)))
            ffmpeg_extract_subclip(full_path, 0, 59, targetname=new_path)
            os.remove(full_path)
            full_path = new_path
        
        xd = self.api.prepare_direct(item["userid"], full_path, itemcode)

        try:
            self.api.send_direct(xd, itemcode)
        except: # Exception as e:
            rnd = random.randint(1, 20) 
            time.sleep(rnd)
            self.api.send_direct(xd, itemcode)
        
        user = self.cfg.get_user(item["userid"])
        if len(user["downloaded_from"]) == 0:
            self.api.sendMessage(str(item["userid"]), self.PROMOTE_MESSAGE)
            self.counter += 1
            logging.info("Welcomed @{u}!".format(u=item["username"]))
        
        self.cfg.user_add_download(item["userid"], item["username"], item["download_from"])
        logging.info("{d} successfully downloaded a {t} from @{u}".format(d=item["username"], t=itemtype, u=item["download_from"]))

        logging.info("Timespan since sent {t}: {s}ms".format(t=itemtype, s=str((time.time() * 1000 // 1) - item["sent"] // 1000)))
        self.delay.capture_delay(int(time.time() - item["sent"] // 1000000), item["priority"])
        if os.path.exists(full_path):
            os.remove(full_path)

    def upload_worker_func(self):
        while self.running:
            if len(self.queue) == 0:
                time.sleep(1)
                continue

            self.queue.sort(key=self.extract_priority, reverse=True)

            item = {}
            filename = None
            full_path = ""
            try:
                item = self.queue[0]
                if item["priority"] > 1:
                    self.sleep = [5, 15]
                rnd = random.randint(self.sleep[0], self.sleep[1]) 
                time.sleep(rnd)
                filename = str(int(round(time.time() * 10000)))
                self.upload_file(item, filename, item["media_type"])

                self.sleep = [10, 30]
                self.queue.remove(item)
            except Exception as e:
                if os.path.exists(full_path):
                    os.remove(full_path)
                logging.error("Error with @{u} {er}".format(er=str(e), u=item["username"]))
                if not "few minutes" in str(e):
                    self.queue.remove(item)
                self.reload_api()
                self.sleep = [30, 120]
            time.sleep(1)


class InboxItem(object):
    def __init__(self, json):
        self.json = json
        self.item = json["items"][0]
        self.users = json["users"]
        self.is_group = json["is_group"]
        self.item_type = self.item["item_type"]
        self.author_id = 0
        if len(self.users) > 0:
            self.author_id = self.users[0]["pk"]
        self.timestamp = self.item["timestamp"]


    def get_media(self):
        location = self.item[self.item_type]
        if self.item_type == "story_share":
            location = location["media"]
        elif self.item_type == "felix_share":
            location = location["video"]

        return location

    def get_media_type(self):
        if self.item_type != "media_share" and self.item_type != "story_share" and self.item_type != "felix_share" :
            return 0

        return self.get_media()["media_type"]

    def get_item_poster(self):
        type = self.get_media_type()
        if type == 0:
            return self.author_id
        name = "~unkown"
        if 0 < type < 3:
            name = self.get_media()["user"]["username"]
        if type == 8:
            name = self.item["media_share"]["user"]["username"]
        return name

    def get_video_url(self):
        url = self.get_media()["video_versions"][0]["url"]
        return url

    def get_image_url(self):
        url = self.get_media()["image_versions2"]["candidates"][0]["url"]
        return url

    def get_multipost_url(self, items, num):
        item = items[num - 1]
        if(item["type"] == 2):
            return item["url"]
        else:
            return "error"
    
    def get_multipost_length(self):
        return len(self.item["media_share"]["carousel_media"])

    def get_multipost_json(self):
        jf = {}
        jf["author_id"] = self.author_id
        jf["download_from"] = self.get_item_poster()
        jf["items"] = []
        for x in self.item["media_share"]["carousel_media"]:
            if(x["media_type"] == 2):
                jf["items"].append({"type": x["media_type"],
                                    "url": x["video_versions"][0],
                                    "duration": x["video_duration"]})
            else:
                jf["items"].append({"type": x["media_type"],
                                    "url": x["image_versions2"][0]})
        return jf


class InboxHandler(object):
    def __init__(self, API, config, delay, admins, uploader, d_uploader):
        self.api = API
        self.cfg = config
        self.delay = delay
        self.count = 0
        self.uploader_list = uploader
        self.uploader = self.uploader_list[0]

        self.admins = admins

        self.first = True

    def is_inbox_valid(self, json_inbox):
        millis = time.time() // 1000
        try:
            snapshot = json_inbox["snapshot_at_ms"] // 1000000
        except Exception:
            snapshot = 0
    
        return millis == snapshot

    def is_multipost_expected(self, userid):
        return os.path.exists(Path("./multi/{u}.json".format(u=str(userid))))

    def run(self):
        while True:
            try:
                try:
                    # TODO: change to push notification based
                    # currently will only retreive the latest message
                    # ignoring the previous one if user send multiple within 15 second period
                    self.handle_inbox()
                    time.sleep(15)
                except Exception as e:
                    logging.error("Handle Inbox crashed:  {0}".format(str(e)))
                    time.sleep(10)
            except:
                time.sleep(10)
        for u in self.uploader_list:
            u.running = False
        logging.error("dead, oof")

    def get_uploader(self):
        upl = self.uploader_list[0]
        for u in self.uploader_list:
            if len(upl.queue) > len(u.queue):
                upl = u

        return upl

    def is_post_queued(self, media_id, username):
        for upl in self.uploader_list:
            if upl.queue_contains_post(media_id, username):
                return True
        return False

    def queue_total(self, do_count=False):
        total = 0
        for upl in self.uploader_list:
            q = len(upl.queue)
            total += q
            if do_count:
                print(str(q), end=" ")
        if do_count:
            print("Total {0}".format(total))
        return total
            
#item handler
    def handle_video(self, username, item, same_queue=False, videojson = None, bypass = False):
        user = self.cfg.get_user(item.author_id)
        if bypass != True and user["latest_item_time"] == item.timestamp:
            return
        self.cfg.user_set_itemtime(item.author_id, username, item.timestamp)

        if not bypass and self.is_post_queued(item.get_media()["pk"], username):
            self.api.sendMessage(str(item.author_id), "That post is already in the queue.")
            return

        if bypass == False:
            self.do_delay_ad(username, item)

        if videojson == None:
            url = item.get_video_url()
            duration = item.get_media()["video_duration"]
        else:
            url = videojson["video_versions"][0]["url"]
            duration = videojson["video_duration"]

        if duration >= 70:
            self.api.sendMessage(str(item.author_id), Language.get_text("video_to_long"))
            return

        uploader = self.uploader
        
        if not same_queue:
            uploader = self.get_uploader()
            self.uploader = uploader
            
        uploader.send_media(url, item.item["item_id"], 2, item.get_media()["pk"], str(item.author_id),  username, item.get_item_poster(), item.timestamp, cut = duration >= 60)
        logging.info("Added @{u} to queue".format(u=username))

    def handle_text(self, username, item, text = ""):
        # change item.author_id to actual user
        if self.cfg.get_user(item.author_id)["latest_item_time"] == item.timestamp:
            return
        self.cfg.user_set_itemtime(item.author_id, username, item.timestamp)

        if text == "":
            try:
                text = item.item["text"]
            except:
                pass
        #ADMINCOMMANDS
        if username in self.admins:  
            if text.startswith("!upgrade"):
                pusername = text.replace("!upgrade ", "")
                now = self.cfg.upgrade_priority(pusername)
                self.api.sendMessage(str(item.author_id), "@{u} now has priority lvl {lv}".format(u=pusername, lv = now))
            elif text.startswith("!downgrade"):
                pusername = text.replace("!downgrade ", "")
                now = self.cfg.downgrade_priority(pusername)
                self.api.sendMessage(str(item.author_id), "@{u} now has priority lvl {lv}".format(u=pusername, lv = now))
            elif text.startswith("!remove"):
                pusername = text.replace("!remove ", "")
                total = 0
                for upl in self.uploader_list:
                    for i in upl.queue:
                        if i["username"] == pusername:
                            total += 1
                            upl.queue.remove(i)
                self.api.sendMessage(str(item.author_id), "Removed {t} queue items from that user!".format(t=total))
            elif text.startswith("!reset"):
                self.delay.reset_delay()
                self.api.sendMessage(str(item.author_id), "Resetted!")
            elif text.startswith("!most"):
                result = {}
                for u in self.uploader_list:
                    for q in u.queue:
                        if q["username"] not in result.keys():
                            result[q["username"]] = 1
                        else:
                            result[q["username"]] += 1
                xd = sorted(result.items(), key=lambda x: x[1], reverse=True)[:10]

                output = "Top 10 users in download queue:"
                index = 1

                for xitem in xd:
                    output += "\r\n{i}. @{u} ({n} downloads in queue)".format(i=index, u=xitem[0], n=xitem[1])
                    index += 1

                if index == 1:
                    output = "Download queue is empty"

                self.api.sendMessage(str(item.author_id), output)
            elif text.startswith("!day"):
                # todo: add to see custom day
                downloads = self.cfg.get_day_download()
                self.api.sendMessage(str(item.author_id), "{dl} downloads today!".format(dl = downloads))
            elif text.startswith("!top"):
                message = ""
                query = text.replace("!top ", "").split(" ")
                qlen = len(query)
                amount = query[qlen] if qlen > 1 and query[qlen - 1].isdigit() else 5
                username = query[1][1:] if len(query) >= 2 and query[1].startswith("@") else ""

                if text == "!top" or query[0] == "":
                    message = 'How to use !top:\r\n' \
                              'To search for top N amount of post owner account with most downloads, do:\r\n' \
                              '!top owner N\r\n\r\n' \
                              'To search for top N amount of downloader for specifc post owner account, do:\r\n' \
                              '!top owner @username N\r\n\r\n' \
                              'To search for top N amount of downloaders with most downloads, do:\r\n' \
                              '!top downloader N\r\n\r\n' \
                              'To search for downloader\'s top N amount of downloads from post owner account, do:\r\n' \
                              '!top downloader @username N\r\n\r\n' \
                              'note: N is optional, default 5'
                elif query[0] == "owner":
                    message = self.cfg.get_post_owner_info(username, amount)
                elif query[0] == "downloader":
                    message = self.cfg.get_post_downloader_info(username, amount)
                self.api.sendMessage(str(item.author_id), message)
            elif text == "!delay":
                msg = ""
                for i in range(0, 100):
                    d = self.delay.get_delay(i)
                    if d != 0:
                        msg += "Priority Lv {lvl} - {delay}s \r\n".format(lvl=i, delay=d)
                if msg == "":
                    msg = "Delay is empty (no delay)"
                self.api.sendMessage(str(item.author_id), msg)
        return

    def handle_link(self, username, item):
        if username in self.admins: 
            self.handle_text(username, item, item.item["link"]["text"]) 
        if self.cfg.get_user(item.author_id)["latest_item_time"] == item.timestamp:
            return
        self.cfg.user_set_itemtime(item.author_id, username, item.timestamp)


        self.api.sendMessage(str(item.author_id), Language.get_text("links_not_supported"))
        return

    def handle_image(self, username, item, same_queue=False, imagejson = None, bypass = False):
        user = self.cfg.get_user(item.author_id)
        if bypass != True and user["latest_item_time"] == item.timestamp:
            return
            
        self.cfg.user_set_itemtime(item.author_id, username, item.timestamp)


        if not bypass and self.is_post_queued(item.get_media()["pk"], username):
            self.api.sendMessage(str(item.author_id), "That post is already in the queue.")
            return

        if bypass == False:
            self.do_delay_ad(username, item) 

        if imagejson == None:
            url = item.get_image_url()
        else:
            url = imagejson["image_versions2"]["candidates"][0]["url"]

        uploader = self.uploader

        if not same_queue:
            uploader = self.get_uploader()
            self.uploader = uploader

        uploader.send_media(url, item.item["item_id"], 1, item.get_media()["pk"], str(item.author_id),  username, item.get_item_poster(), item.timestamp, cut = False)
        logging.info("Added @{u} to queue".format(u=username))

    def handle_placeholder(self, username, item):
        if self.cfg.get_user(item.author_id)["latest_item_time"] == item.timestamp:
            return
        self.cfg.user_set_itemtime(item.author_id, username, item.timestamp)
        if "Unavailable" in item.get_media()["title"]:
            msg = item.get_media()["message"]
            if "@" in msg:
                username_requested = "".join([i for i in msg.split() if i.startswith("@")][0])[1:]
                self.cfg.requested_add_request(username_requested, item.author_id)
            
                self.api.sendMessage(str(item.author_id), Language.get_text("requested"))
                return
            elif "deleted" in msg:
                self.api.sendMessage(str(item.author_id), Language.get_text("deleted"))
            else:
                self.api.sendMessage(str(item.author_id), Language.get_text("blocked"))
        return

    def handle_story(self, username, item):
        try:
            title = item.item["story_share"]["title"]
            msg = item.item["story_share"]["message"]
            reason = item.item["story_share"]["reason"]
        except :
            title = "nope"
            # message = None

        if title != "nope":
            if reason != 4:
                return
            #Not following
            if self.cfg.get_user(item.author_id)["latest_item_time"] == item.timestamp:
                return
            self.cfg.user_set_itemtime(item.author_id, username, item.timestamp)
            username_requested = "".join([i for i in msg.split() if i.startswith("@")][0])[1:]
            self.cfg.requested_add_request(username_requested, item.author_id)
            self.api.sendMessage(str(item.author_id), Language.get_text("requested"))
            return

        if item.get_media_type() == 2:
            self.handle_video(username, item)
        elif item.get_media_type() == 1:
            self.handle_image(username, item)

    def handle_media_share(self, username, item):
        if self.cfg.get_user(item.author_id)["latest_item_time"] == item.timestamp:
            return

        if item.get_media_type() == 2:
            self.handle_video(username, item)

        elif item.get_media_type() == 1:
            self.handle_image(username, item)

        elif item.get_media_type() == 8:
            if self.cfg.get_user(item.author_id)["latest_item_time"] == item.timestamp:
                return
            if self.queue_total() > 2000:
                self.api.sendMessage(str(item.author_id), "Slideposts are currently disabled due to heavy server load. Please come back later.")
                self.cfg.user_set_itemtime(item.author_id, username, item.timestamp)
                return
            for i in item.get_media()["carousel_media"]:
                if i["media_type"] == 2:
                    self.handle_video(username, item, True, i, True)
                elif i["media_type"] == 1:
                    try:
                        self.handle_image(username, item, True, i, True)
                    except: # Exception as e:
                        print("skip")
                    

    def handle_profilepic(self, username, item):
        if self.cfg.get_user(item.author_id)["latest_item_time"] == item.timestamp:
            return
        self.cfg.user_set_itemtime(item.author_id, username, item.timestamp)
        if item.item["profile"]["has_anonymous_profile_picture"]:
            self.api.sendMessage(str(item.author_id), "That profile picture is anonymous")
        url = item.item["profile"]["profile_pic_url"]
        self.uploader.send_media(url, item.item["item_id"], 1, str(item.author_id),  username, item.item["profile"]["username"], item.timestamp, cut = False)
        logging.info("Added @{u} to queue".format(u=username))


    def do_delay_ad(self, username, item):
        user = self.cfg.get_user(item.author_id)
        priority = user["priority"]
        delay = self.delay.get_delay(priority)
        print("user " + username + " " + str(delay))
        if delay > 300:
            uprankdelay = self.delay.get_delay(priority+1)
            if uprankdelay > 150:
                return
            self.api.sendMessage(str(item.author_id), "There are {q} people in the queue. Let an admin upgrade your priority".format(q=self.queue_total()))

    def handle_inbox(self):
        print("handle inbox")
        num = 20
        if self.first:
            num = 50
            self.first = False
        self.api.getv2Inbox(num)
        with  open(Path("last.json"), "w+") as fp:
            json.dump(self.api.LastJson, fp)
        inbox = self.api.LastJson
        if not self.is_inbox_valid(inbox):
            logging.warning("Invalid inbox.. sleeping 10s")
            time.sleep(10)
            return

        for i in inbox["inbox"]["threads"]:
            try:
                username = i["users"][0]["username"]
            except :
                username = ""

            item = InboxItem(i)
            if item.is_group:
                continue
            self.cfg.check_user(username, item.author_id)

            if item.item_type == "text":
                self.handle_text(username, item)

            elif item.item_type == "link":
                self.handle_link(username, item)

            elif item.item_type == "profile":
                self.handle_profilepic(username, item)

            elif item.item_type == "placeholder":
                self.handle_placeholder(username, item)

            elif item.item_type == "story_share":
                self.handle_story(username, item)

            elif item.item_type == "media_share":
                self.handle_media_share(username, item)

        if inbox["pending_requests_total"] == 0:
            time.sleep(1)
            self.queue_total(True)
            x = 0
            for upl in self.uploader_list:
                path = "uploader{0}_queue".format(str(x))
                with  open(path, "w+") as fp:
                    json.dump(upl.queue, fp)
                x+=1
            return

        print("Now pending..")
        self.api.get_pending_inbox()
        inbox = self.api.LastJson
        for i in inbox["inbox"]["threads"]:
            try:
                username = i["users"][0]["username"]
            except :
                username = ""

            item = InboxItem(i)
            self.api.approve_pending_thread(i["thread_id"])
            self.cfg.check_user(username, item.author_id)

            if item.item_type == "text":
                self.handle_text(username, item)

            elif item.item_type == "link":
                self.handle_link(username, item)

            elif item.item_type == "placeholder":
                self.handle_placeholder(username, item)

            elif item.item_type == "story_share":
                self.handle_story(username, item)

            elif item.item_type == "media_share":
                self.handle_media_share(username, item)

def Login(username, password, admins, promote_message):
    cfg = Storage()
    delay = Delay()
    # sessionpath = Path("sessions/{u}.session".format(u = username))

    mainstorage = APIStorage(0)
    api = mainstorage.load(username, password)

    if not api.isLoggedIn:
        logging.error("Failed to login")
        exit()

    uploaders = []
    for x in range(1, 3):
        queuepath = Path("uploader{0}_queue".format(x))

        substorage = APIStorage(x)
        uapi = substorage.load(username, password)
        test_upl = Uploader(uapi, cfg, delay, x, substorage, promote_message)

        if os.path.exists(queuepath):
            test_upl.queue = json.load(open(queuepath))

        test_upl.start()
        uploaders.append(test_upl)


    inbox = InboxHandler(api, cfg, delay, admins, uploaders, [])
    inbox.run()