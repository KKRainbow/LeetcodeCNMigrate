import aiohttp, bs4
import asyncio, functools
import json, urllib, re
import codecs, base64
import os, datetime

CN_URL='https://leetcode-cn.com'
EN_URL='https://leetcode.com'

def get_modification_date(filename):
    t = os.path.getmtime(filename)
    return datetime.datetime.fromtimestamp(t)

def cache_result(cache_file, expire_duration):
    def decorator(f):
        @functools.wraps(f)
        async def wrapper(self, *args, **kwargs):
            fname = cache_file + self.name
            try:
                t = get_modification_date(fname)
                if datetime.datetime.now() - t > expire_duration:
                    raise Exception()
                with open(fname, 'r') as fd:
                    res = json.load(fd)
                    print("successful read cache")
            except Exception as e:
                print("failed to read cache", e)
                res = await f(self, *args, **kwargs)
                with open(fname, 'w+') as fd:
                    json.dump(res, fd)
            return res
        return wrapper
    return decorator

class NotLoginException(Exception):
    pass

def login_required(f):
    @functools.wraps(f)
    async def wrapper(self, *args, **kwargs):
        res = None
        try:
            if not self.logged:
                self.load_cookie()
            res = await f(self, *args, **kwargs)
        except NotLoginException:
            try:
                if not self.load_cookie():
                    await self.login()
                else:
                    print("Load cookie succeed", self.url)
                res = await f(self, *args, **kwargs)
            except NotLoginException:
                await self.login()
                res = await f(self, *args, **kwargs)
        return res
    return wrapper

class Leetcode:
    def __init__(self, url): 
        self.url = url
        self.session = aiohttp.ClientSession()
        self.cookies = self.session.cookie_jar
        self.name = base64.b64encode(self.url.encode(encoding='utf-8')).decode(encoding='utf-8')
        self.logged = False

    async def close(self):
        await self.session.close()

    def get_api_url(self, api):
        return "{0}/{1}/".format(self.url, api)

    def get_cookie(self, key):
        for cookie in self.cookies:
            if cookie.key == key:
                return cookie.value
        return None

    def cookie_path(self):
        n = "./{0}.cookie".format(self.name)
        with open(n, 'a+'):
            pass
        return n

    def save_cookie(self):
        self.cookies.save(self.cookie_path())

    def load_cookie(self):
        try:
            self.cookies.load(self.cookie_path())
            print("Load cookie succeeded", self.url)
        except Exception as e:
            print("Load cookie failed", self.url)
            return False
        self.logged = True
        return True

    async def login(self):
        url = self.get_api_url('accounts/login')
        name = { 'name' : 'csrfmiddlewaretoken' }

        username = input(url + ' Username: ')
        password = input(url + ' Password: ')

        resp = await self.session.get(url, ssl=False)

        soup = bs4.BeautifulSoup(await resp.text(), 'html.parser')
        for e in soup.find_all( 'input', attrs=name ):
            csrf = e[ 'value' ]
            break

        headers = { 'referer' : url }
        data = {
            'login': username,
            'password': password,
            'csrfmiddlewaretoken': csrf,
        }

        resp = await self.session.post( url, data=data, headers=headers, ssl=False)

        if self.get_cookie('LEETCODE_SESSION'):
            self.logged = True
            self.save_cookie()
            print('Welcome %s!' % username)
        else:
            self.logged = False

    @login_required
    async def get_all_submissions(self, start=0, total=9999999):
        """
        {
            'lang': 'cpp',
            'time': '2 months, 1 week',
            'status_display': 'Accepted',
            'runtime': '10 ms',
            'url': '/submissions/detail/136449702/',
            'is_pending': 'Not Pending',
            'title': 'Linked List Cycle'
        }
        """
        limit = 20 # 每次取20个，这是leetcode的上限
        assert limit <= 20 # leetcode limit
        url = self.get_api_url('api/submissions')

        async def f(offset, limit, lastkey=''):
            ret = await self.session.get(url, ssl=False, params=dict(
                offset=offset,
                limit=limit,
                lastkey=lastkey,
            ))
            return await ret.json()

        j = await f(start, limit)
        if 'submissions_dump' not in j:
            raise NotLoginException

        submissions = j["submissions_dump"]
        while j["has_next"] and len(submissions) < total:
            j = await f(0, 0, j["last_key"])
            submissions += j["submissions_dump"]

        return submissions[:total]

    @cache_result("all_problems", datetime.timedelta(minutes=30))
    @login_required
    async def get_all_problems(self):
        url = self.get_api_url('api/problems/all')
        ret = await self.session.get(url, ssl=False)
        j = await ret.json(content_type=None)
        if "user_name" not in j or len(j["user_name"]) == 0:
            raise NotLoginException()
        return j

    @login_required
    async def get_submission_detail(self, url):
        url = self.get_api_url(url)
        ret = await self.session.get(url, ssl=False)
        text = await ret.text()
        soup = bs4.BeautifulSoup(text, 'html.parser')

        js = soup.find("script", text=re.compile(r'var pageData ='))
        if js is None or len(js) == 0:
            raise NotLoginException()

        match = re.search(r'var pageData = ({.*^});$', str(js), re.DOTALL | re.MULTILINE)
        jsonText = match.group(1)
        jsonText = re.sub(r'parseInt\(\'(\d+)\', 10\)', r'\1', jsonText)

        # 给属性的名字加上双引号
        jsonText = re.sub(r'^\s*(\w+)\s*:', r'"\1":', jsonText, flags=re.MULTILINE)

        # 把属性值得第一个单引号改成双引号
        jsonText = re.sub(r":\s*'", ': "', jsonText, flags=re.MULTILINE)

        # 把属性值得第二个单引号改成双引号
        jsonText = re.sub(r'\'(,?)$', r'"\1', jsonText, flags=re.MULTILINE)

        # 防止某些属性作为数组的最后一个却还加了逗号
        jsonText = re.sub(r'",$\s*},', '" },', jsonText, flags=re.MULTILINE | re.DOTALL)
        try:
            j = json.loads(jsonText)
        except:
            raise NotLoginException()

        if "submissionCode" not in j:
            raise NotLoginException()

        return j

    @staticmethod
    def get_code_from_submission(j):
        return codecs.decode(j["submissionCode"], 'unicode_escape')

    @login_required
    async def submit_answer(self, title, question_id, code, lang):
        url = self.get_api_url('problems/{0}/submit'.format(title))
        print("url: {4}, title: {0}, question_id: {1}, code: {2}, lang: {3}".format(title, question_id, len(code), lang, url))

        referer = self.url + '/problems/%s/description/' % title

        ret = await self.session.post(url, ssl=False, json=dict(
            data_input='',
            judge_type='large',
            lang=lang,
            question_id=str(question_id),
            test_mode=False,
            typed_code=code,
        ), headers={
            'referer' : referer,
            'content-type' : 'application/json',
            'x-csrftoken' : self.get_cookie('csrftoken'),
            'x-requested-with' : 'XMLHttpRequest',
        })

        try:
            j = await ret.json(content_type=None)
            if "error" in j:
                raise NotLoginException()
            else:
                return j
        except NotLoginException:
            raise
        except:
            return await ret.text()

    @login_required
    async def get_submit_result(self, sid, timeout=30):
        url = self.url + '/submissions/detail/%s/check/' % sid
        for i in range(timeout):
            await asyncio.sleep( 1 )
            ret = await self.session.get( url )
            data = await ret.json(content_type=None)
            print("submit check result", data)
            if data.get( 'state' ) == 'SUCCESS':
                break
        else:
            data = { 'error': '< network timeout >' }

        return sid, data

async def main():
    cn = Leetcode(CN_URL)
    en = Leetcode(EN_URL)
    print("getting all problems")
    problems = await cn.get_all_problems()
    problems_dict = {p["stat"]["question__title"]: p for p in problems["stat_status_pairs"]}

    for i in range(0, 9999999, 20):
        print("getting submissions")
        subs = await en.get_all_submissions(i, 20)
        if len(subs) == 0:
            break
        print("getting accepted submissions") 
        ac_submissions = [s for s in subs if s["status_display"] == "Accepted"]

        for ac_sub in ac_submissions:
            print("Processing ", ac_sub["title"])
            if ac_sub["title"] not in problems_dict:
                print("中文站没有该题目：", ac_sub["title"])
                continue

            p = problems_dict[ac_sub["title"]]
            if p["status"] == "ac":
                print(ac_sub["title"], "已经AC了")
                continue
            else:
                print("status", ac_sub["title"], p["status"])

            detail = await en.get_submission_detail(ac_sub["url"])
            title = p["stat"]["question__title_slug"]
            question_id = p["stat"]["question_id"]
            code = Leetcode.get_code_from_submission(detail)
            lang = ac_sub["lang"]
            for trial in range(3):
                sub = await cn.submit_answer(title, question_id, code, lang)
                try:
                    if "submission_id" in sub:
                        print(await cn.get_submit_result(sub["submission_id"]))
                        await asyncio.sleep(5)
                        break
                    else:
                        print("提交失败", sub)
                        await asyncio.sleep(3)
                except:
                    continue
            print("end")
    await cn.close()
    await en.close()
    
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())

