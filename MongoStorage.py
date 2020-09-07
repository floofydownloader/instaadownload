import os
import datetime
import Language
import pymongo
import requests

from Api import InstagramAPI

# choosing mongoDB as it stores more data compared with postgres
# https://medium.com/@shivam270295/estimating-average-document-size-in-a-mongodb-collection-953b0788fac0
# instagram redo https://stackoverflow.com/a/60744028

# Single MongoDB instance

class MongoDB(object):
    def __init__(self):
        # the replace is for temporary fix issue
        client = pymongo.MongoClient(os.environ["MONGODB_URI"].replace("retryWrites=true", "retryWrites=false"), retryWrites=False)
        self.db = client.get_default_database()

SingleMongoDB = MongoDB()

# Storage class for Days, Users and Requests data and statistics

class Storage(object):
    def __init__(self):
        self.DEFAULT_PRIORITY = 1
        self.init_db()
        self.init_collection_info()

    def init_db(self):
        self.db = SingleMongoDB.db
        self.users = self.db["users"]
        self.days = self.db["days"]
        self.requests = self.db["requests"]

        # define index for collections, ensure better searching and prevent duplication
        self.users.create_index("username")
        self.days.create_index("date")
        self.requests.create_index("username")

    def init_collection_info(self):
        self.collection_info_list = {
            "users": {
                "name": "users",
                "array_name": "downloaded_from",
                "action_text": "downloads",
                "aggregate_user": "downloaders for post account",
                "aggregate_all": "downloaded post accounts",
                "query_user": "downloaded post account for downloader",
                "query_all": "downloaders"
            },
            "requests": {
                "name": "requests",
                "array_name": "requestors",
                "action_text": "requests",
                "aggregate_user": "requested post accounts for requestor",
                "aggregate_all": "requestors",
                "query_user": "requestors for post account",
                "query_all": "requested post account"
            }
        }

    # DAY STATS

    def get_day_download(self, day = None):
        date = None
        if day is None:
            date = datetime.date.today()
        else:
            day = day.split("-")
            day.reverse()
            day = "-".join(day)
            date = datetime.date.isoformat(day)
        date = datetime.datetime.combine(date, datetime.time())
        date_res = self.days.find_one({ "date": date })
        return date_res["counts"] if date_res is not None else 0

    def day_add_download(self):
        date = datetime.datetime.combine(datetime.date.today(), datetime.time())
        self.days.update_one({ "date": date }, { "$inc": { "counts": 1 } }, upsert = True)

    # USER DATA

    def format_userid(self, userid):
        return userid if isinstance(userid, int) else int(userid) if userid.isdigit() else 0

    def create_user(self, userid, username):
        userData = {
            "userid": userid,
            "username": username,
            "priority": self.DEFAULT_PRIORITY,
            "latest_item_time": 0,
            self.collection_info_list["users"]["array_name"]: []
        }
        user = self.users.insert_one(userData)
        userData["_id"] = user.inserted_id
        return userData

    def modify_user(self, search_query, modify_query, none_insert = False):
        user = self.users.find_one_and_update(search_query, modify_query, upsert = none_insert, return_document = pymongo.ReturnDocument.AFTER)
        return user
    
    def internal_get_user(self, userid, create = False, username = ""):
        user = None

        if userid is not None and userid > 0:
            user = self.users.find_one({ "userid": userid })
        
        if user is None:
            user = self.users.find_one({ "username": username })

        if user is None and create and username != "":
            return self.create_user(userid, username)
        
        if user is not None:
            set_query = {}
            need_set = False

            if user["userid"] == "" and userid != "":
                set_query["userid"] = userid
                need_set = True
            if user["username"] == "" and username != "" and username != "@UNKNOWN@":
                set_query["username"] = username
                need_set = True
            
            if need_set:
                user = self.users.find_one_and_update({ "_id": user["_id"] }, { "$set": set_query }, return_document = pymongo.ReturnDocument.AFTER)
        return user

    def get_user(self, userid):
        userid = self.format_userid(userid)
        return self.internal_get_user(userid)

    def user_add_download(self, userid, username, downloaded_from):
        userid = self.format_userid(userid)
        user = self.internal_get_user(userid, create = True, username = username)
        if user == None:
            return False

        self.increase_count(self.collection_info_list["users"], "userid", userid, downloaded_from)

        # add day download count
        self.day_add_download()

        return True

    def check_user(self, username, userid = ""):
        userid = self.format_userid(userid)
        return self.internal_get_user(userid, create = True, username = username)

    def user_set_itemtime(self, userid, username, item_time):
        userid = self.format_userid(userid)
        user = self.check_user(username, userid)
        self.modify_user({ "_id": user["_id"] }, {"$set": { "latest_item_time": item_time } })

    def upgrade_priority(self, username, amount = 1):
        amount = int(amount)
        user = self.check_user(username)
        user = self.modify_user({ "_id": user["_id"] }, {"$inc": { "priority": amount }})
        return user["priority"]

    def downgrade_priority(self, username, amount = 1):
        amount = int(amount)
        user = self.check_user(username)
        user = self.modify_user({ "_id": user["_id"] }, {"$inc": { "priority": -amount }})
        return user["priority"]

    # SHARED PARTS

    def increase_count(self, collection_info, search_key, search_value, count_username):
        db_name = collection_info["name"]
        array_name = collection_info["array_name"]
        action_text = collection_info["action_text"]
        array_username = "{}.username".format(array_name)
        ref_action_text = "{}.$.{}".format(array_name, action_text)

        # first add in { username, action: 0 } if array does not have username
        self.db[db_name].update_one({
            search_key: search_value,
            array_username: { "$ne": count_username }
        }, {
            "$push": { 
                array_name: { "username": count_username, action_text: 0 }
            }
        })

        # then increase by 1
        self.db[db_name].update_one({
            search_key: search_value,
            array_username: count_username
        }, {
            "$inc": { ref_action_text: 1 }
        })

    # REQUEST DATA

    def create_request(self, username):
        requestData = {
            "username": username,
            self.collection_info_list["requests"]["array_name"]: []
        }
        request = self.requests.insert_one(requestData)
        requestData["_id"] = request.inserted_id
        return requestData
    
    def internal_get_request(self, username, create = False):
        if username is None or username == "":
            raise Exception("Username not found!")

        request = self.requests.find_one({ "username": username })

        if request is None and create:
            return self.create_request(username)
        
        return request

    def requested_add_request(self, username, requested_by_username):
        self.internal_get_request(username, create = True)

        self.increase_count(self.collection_info_list["requests"], "username", username, requested_by_username)

    # STATS FUNCTION

    def aggregate_query(self, collection_name, array_name, count_name, username, top_amount):
        # pipeline for top 5 most post/requested account: unwind, group, sort and limit
        # pipeline for top 5 most downloaders/requestors for specific post/requested account: unwind, match, sort, limit and group
        array_username = "{}.username".format(array_name)
        full_count_name = "{}.{}".format(array_name, count_name)

        ref_array_name = "$" + array_name
        aref_rray_username = "$" + array_username
        ref_full_count_name = "$" + full_count_name

        has_username = username != "" and username is not None

        aggregate_pipe = [ { "$unwind": { "path": ref_array_name } } ]
        group_pipe = {
            "_id": aref_rray_username,
            "total": { "$sum": ref_full_count_name }
        }
        sort_pipe = {}

        if has_username:
            group_pipe[array_name] = {
                "$push": {
                    "username": "$username", 
                    count_name: ref_full_count_name
                }
            }
            sort_pipe[full_count_name] = -1
            aggregate_pipe.append({ "$match": { array_username: username } })
        else:
            sort_pipe["total"] = -1
            aggregate_pipe.append({ "$group": group_pipe })
        
        aggregate_pipe.append({ "$sort": sort_pipe })

        aggregate_pipe.append({ "$limit": top_amount })

        # TODO: fix count, as previous limit pipeline removed rest
        if has_username:
            aggregate_pipe.append({ "$group": group_pipe })
        
        return self.db[collection_name].aggregate(aggregate_pipe)

    def format_text(self, array, username_key, total_key, action_text):
        index = 1
        output = ""
        
        for item in array:
            output += "\r\n{i}. @{u} ({c} {a})".format(i = index, u = item[username_key], c = item[total_key], a = action_text)
            index += 1

        return output

    def format_output(self, output, extra_info, username, action_text):
        if extra_info == "":
            if username == "" or username is None:
                return Language.get_text("admin.no_data").format(action_text)
            return Language.get_text("admin.no_spec_data").format(a = action_text, u = username)
        return output + extra_info

    def get_aggregated_account_info(self, collection_info, username, top_amount):
        has_username = username != "" and username is not None
        array_name = collection_info["array_name"]
        action_text = collection_info["action_text"]

        results = self.aggregate_query(collection_info["name"], array_name, action_text, username, top_amount)
        output = "Top {c} ".format(c = top_amount)
        extra_info = ""

        if has_username:
            total = 0
            for relation_user in results:
                total = relation_user["total"]
                extra_info += self.format_text(relation_user[array_name], "username", action_text, action_text)
            output += "{o} @{u} (total of {t} {a})".format(o = collection_info["aggregate_user"], u = username, t = total, a = action_text)
        else:
            output += collection_info["aggregate_all"] 
            extra_info += self.format_text(results, "_id", "total", action_text)
        
        return self.format_output(output, extra_info, username, action_text)

    def get_query_account_info(self, collection_info, username, top_amount):
        has_username = username != "" and username is not None
        array_name = collection_info["array_name"]
        action_text = collection_info["action_text"]
        ref_full_count_name = "${}.{}".format(array_name, action_text)

        aggregate_pipe = []
        output = "Top {c} ".format(c = top_amount)
        results = None
        key = ""

        # add filter username pipeline FIRST if username exist
        if has_username:
            aggregate_pipe.append( { "$match": { "username": username } } )
        
        aggregate_pipe += [
            { "$addFields": { "total": { "$sum": ref_full_count_name } } },
            { "$sort": { "total": -1 } },
            { "$limit": top_amount }
        ]
        
        if has_username:
            key = action_text
        else:
            key = "total"
            output += collection_info["query_all"]

        results = self.db[collection_info["name"]].aggregate(aggregate_pipe)
        
        if has_username:
            total = 0
            for result in results:
                total = result["total"]
                results = sorted(result[array_name], key = lambda dl: dl[action_text], reverse = True)[:top_amount]
            output += "{o} @{u} (total of {t} downloads)".format(o = collection_info["query_user"], u = username, t = total)
        
        extra_info = self.format_text(results, "username", key, action_text)

        return self.format_output(output, extra_info, username, action_text)
    
    # USER STATS

    def get_post_owner_info(self, username = "", top_amount = 5):
        return self.get_aggregated_account_info(self.collection_info_list["users"], username, top_amount)

    def get_post_downloader_info(self, username = "", top_amount = 5):
        return self.get_query_account_info(self.collection_info_list["users"], username, top_amount)
    
    # REQUEST STATS

    def get_requestor_info(self, username = "", top_amount = 5):
        return self.get_aggregated_account_info(self.collection_info_list["requests"], username, top_amount)

    def get_requested_info(self, username = "", top_amount = 5):
        return self.get_query_account_info(self.collection_info_list["requests"], username, top_amount)

# Separated API storage class
# TODO: implement encryption

class APIStorage(object):
    def __init__(self, session_id):
        self.session_id = session_id
        self.username = ""
        self.password = ""

        self.sessions = SingleMongoDB.db["sessions"]
        self.sessions.create_index("session_id")
    
    def save(self, instaAPI):
        output_data = {
            "session_id": self.session_id,
            "device_id": instaAPI.device_id,
            "uuid": instaAPI.uuid,
            "isLoggedIn": instaAPI.isLoggedIn,
            "username_id": instaAPI.username_id,
            "rank_token": instaAPI.rank_token,
            "token": instaAPI.token,
            'username': self.username,
            "cookies": self.extract_cookies(instaAPI.s.cookies)
        }
        self.sessions.update_one({ 'session_id': self.session_id },  { "$set": output_data }, upsert=True)

    def load(self, username = "", password = ""):
        self.username = username if self.username == "" else self.username
        self.password = password if self.password == "" else self.password
        instaAPI = InstagramAPI(self.username)

        output_data = self.sessions.find_one({ 'session_id': self.session_id, 'username': self.username })
        if output_data is None:
            instaAPI.login(self.password)
            self.save(instaAPI)
        else:
            instaAPI.device_id = output_data['device_id']
            instaAPI.uuid = output_data["uuid"]
            instaAPI.isLoggedIn = output_data["isLoggedIn"]
            instaAPI.username_id = output_data["username_id"]
            instaAPI.rank_token = output_data["rank_token"]
            instaAPI.isLoggedIn = True
            instaAPI.token = output_data["token"]
            self.to_cookies(output_data['cookies'], instaAPI.s.cookies)
            instaAPI.s.headers.update({ 'Cookie2': '$Version=1',
                                        'Accept-Language': 'en-US',
                                        'Accept-Encoding': 'gzip, deflate',
                                        'Accept': '*/*',
                                        'Content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
                                        'Connection': 'close',
                                        'User-Agent': instaAPI.USER_AGENT })
        
        return instaAPI

    
    def extract_cookies(self, cookies):
        sim_cookies = []

        for cookie in cookies:
            sim_cookie = { 'name': cookie.name, 'value': cookie.value }

            if cookie.expires is not None:
                sim_cookie['expires'] = cookie.expires

            if 'HttpOnly' in cookie._rest:
                sim_cookie['HttpOnly'] = True

            sim_cookies.append(sim_cookie)

        return sim_cookies

    def to_cookies(self, sim_cookies, cookies):
        for sim_cookie in sim_cookies:
            expires = sim_cookie['expires'] if 'expires' in sim_cookie and sim_cookie['expires'] else None
            rest = { 'HttpOnly': None } if 'HttpOnly' in sim_cookie and sim_cookie['HttpOnly'] else None
            discard = sim_cookie['name'] == "urlgen"
            cookies.set(sim_cookie['name'], sim_cookie['value'], expires=expires, rest=rest, secure=True, domain='.instagram.com', discard=discard)


