import requests


ths_cookie_1 = 'user=MDpmYWNreHphbjo6Tm9uZTo1MDA6MzM5MzY0NDk5OjcsMTExMTExMTExMTEsNDA7NDQsMTEsNDA7NiwxLDQwOzUsMSw0MDsxLDEwMSw0MDsyLDEsNDA7MywxLDQwOzUsMSw0MDs4LDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAxLDQwOzEwMiwxLDQwOjI3Ojo6MzI5MzY0NDk5OjE3MDkyOTM5ODg6OjoxNDU5NDM2MTYwOjI2Nzg0MDA6MDoxYTNiYTJhOGQwMjQ1YTRkZWY2YTRkMmQxMTg5ZGVlNTg6ZGVmYXVsdF80OjA%3D; userid=329364499; u_name=fackxzan; escapename=fackxzan; ticket=cb9bdfd37341889a47cb2f391dab5fa1;'
heads = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,'
              'image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Cache-Control': 'max-age=0',
    'Connection': 'keep-alive',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.163 Safari/537.36',
    'Cookie': ths_cookie_1
}


def wen_cai_stock_info_by_question_new(question=None) -> []:
    url = 'http://www.iwencai.com/customized/chart/get-robot-data'
    query_data = {"source": "Ths_iwencai_Xuangu", "version": "2.0", "query_area": "", "block_list": "",
                  "add_info": "{\"urp\":{\"scene\":1,\"company\":1,\"business\":1},\"contentType\":\"json\",\"searchInfo\":true}",
                  "question": question, "perpage": 500, "page": 1, "secondary_intent": "stock",
                  "log_info": "{\"input_type\":\"typewrite\"}", "rsh": "329364499"}
    heads = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,'
                  'image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Cache-Control': 'max-age=0',
        'Connection': 'keep-alive',
        'Host': 'search.10jqka.com.cn',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.163 Safari/537.36',
        'Cookie': ths_cookie_1}
    data = requests.post(url, data=query_data,
                         headers=heads, allow_redirects=False)
    content = data.json()
    state = content['data']['answer'][0]
    pass


def build_wen_cai_stock_info(row, all_quotes: []):
    quote = {'Symbol': row['code'], 'Name': row['股票简称'], 'Market': row['market_code']}
    all_quotes.append(quote)


def wen_cai_stock_info_by_question(question=None) -> []:
    """
    根据问题使用问财经搜索,返回[{Symbol:"",Name:""}]
    其他参考连接：
    parse_data_url = 'https://search.10jqka.com.cn/unified-wap/get-parser-data'
    get_base_data_url = 'https://search.10jqka.com.cn/unified-wap/get-base-data'
    stock_info = 'https://search.10jqka.com.cn/robot-index/get-robot-data'
    self_choose_stock_url = 'http://www.iwencai.com/unifiedwap/unified-wap/v2/result/get-robot-data'
    :param question:
    :return:
    """
    stock_info_pc = 'http://www.iwencai.com/unifiedwap/unified-wap/result/get-stock-pick'
    query_data = {
        'perpage': 500,
        'version': '2.0',
        'source': 'ths_mobile_iwencai',
        'user_id': '329364499',
        'user_name': 'fackxzan',
        ' question': question,
        'direct_mode': '',
        'secondary_intent': '',
        'add_info': ' {\"urp\":{\"scene\":3,\"company\":1,\"business\":1},\"contentType\":\"json\"}',
        '_': '1588395395072'
    }

    heads = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,'
                  'image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Cache-Control': 'max-age=0',
        'Connection': 'keep-alive',
        'Host': 'search.10jqka.com.cn',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.163 Safari/537.36',
        'Cookie': ths_cookie_1}

    # data = requests.get(stock_info_pc, params=query_data, headers=heads,allow_redirects=False)
    data = requests.post(stock_info_pc, data=query_data,
                         headers=heads, allow_redirects=False)
    content = data.json()
    state = content['data']['data']
    all_quotes = []
    if len(state) > 0:
        for stock_info in state:
            # print(stock_info)
            stock_info_code = stock_info['股票代码']
            stock_info_code_array = str(stock_info_code).split(".")
            quote = {
                'Symbol': stock_info_code_array[0], 'Market': stock_info_code_array[1], 'Name': stock_info['股票简称']}
            if '概念解析' not in dict(stock_info).keys():
                quote['stock_reason'] = ''
            else:
                quote['stock_reason'] = stock_info['概念解析']
            if 'st' not in str(quote['Name']).lower() and not str(quote['Symbol']).startswith('68') and not str(
                    quote['Symbol']).startswith('83') and not str(quote['Symbol']).startswith('87'):
                all_quotes.append(quote)
    # 增加大盘指数
    # sz_quote = {"Symbol": '000001', 'Name': '上证指数'}
    # all_quotes.append(sz_quote)
    # shenz_quote = {"Symbol": '399001', 'Name': '深证指数'}
    # all_quotes.append(shenz_quote)
    # cy_quote = {"Symbol": '399006', 'Name': '创业板'}
    # all_quotes.append(cy_quote)
    #print(all_quotes)
    return all_quotes


def add_ths_self_choice(code: str):
    import time
    import json
    url = 'https://t.10jqka.com.cn/newcircle/group/modifySelfStock/?callback=modifyStock&op=add&stockcode={}&_={}'
    time_stamp = int(time.time())
    url = url.format(code, time_stamp)
    print(url)
    response = requests.get(url, headers=heads, allow_redirects=True)
    result = response.text.encode().decode('unicode_escape')
    result = json.loads(result)

    print(result)  # 输出中文字符"中"
    pass


def get_ths_self_choice(ths_cookie=None):
    import json
    url = 'https://t.10jqka.com.cn/newcircle/group/getSelfStockWithMarket/'
    heads_1 = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,'
                  'image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Cache-Control': 'max-age=0',
        'Connection': 'keep-alive',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.163 Safari/537.36',
        'Cookie': ths_cookie
    }
    response = requests.get(url, headers=heads_1, allow_redirects=True)
    result = response.text.encode().decode('unicode_escape')
    result = json.loads(result)
    quotes = []
    result = result['result']
    df = ak.stock_zh_a_spot_em()
    if (len(result) > 0):
        for quote in result:
            quote["Symbol"] = quote['code']
            name_list = df[df['代码'] == quote['code']]['名称'].values
            if len(name_list) > 0:
                quote["Name"] = name_list[0]
                quotes.append(quote)
    return quotes


if __name__ == '__main__':
    print(wen_cai_stock_info_by_question(question='多方炮，非st，非科创板，非北交所，涨幅大于1%'))
  
