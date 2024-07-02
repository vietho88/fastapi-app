from typing import List, Dict
from fastapi import FastAPI
from playwright.async_api import async_playwright
from pydantic import BaseModel
import aiohttp
import uvicorn
import pybase64
from datetime import datetime
import asyncio
import random


app = FastAPI()

class CCCDList(BaseModel):
    cccd_list: List[str]

class MSTList(BaseModel):
    mst_list: List[str]

async def fetch_captcha_text(session, img_data: str) -> str:
    url = "http://117.2.155.191:7010/ocr_tracuunnt"
    payload = {'imgfile': img_data, 'model': '1'}
    async with session.post(url, json=payload) as response:
        return await response.text()

async def crop_captcha_ocr(session, img_str: str) -> str:
    return await fetch_captcha_text(session, img_str)

async def process_single_request(input_value: str, sem: asyncio.Semaphore, input_type='cccd') -> Dict:
    async with sem:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto('https://tracuunnt.gdt.gov.vn/tcnnt/mstcn.jsp')
            attempt_count = 0
            captchas_fetched = []
            async with aiohttp.ClientSession() as session:
                while attempt_count <= 3:
                    input_selector = '//*[@id="module3Content"]//form/table/tbody/tr[2]/td[2]/input' if input_type == 'mst' else '//*[@id="module3Content"]/div/form/table/tbody/tr[5]/td[2]/input'
                    captcha_selector = '//*[@id="captcha"]'
                    submit_selector = '//*[@id="module3Content"]/div/form/table/tbody/tr[7]/td[2]/div/input[1]'
                    element_captcha_img = page.locator('//*[@id="module3Content"]/div/form/table/tbody/tr[6]/td[2]/table/tbody/tr/td[2]/div/img')                     
                    random_number = random.randint(1, 20) 
                    capcha = f'captcha_{str(random_number)}'
                    await element_captcha_img.screenshot(path=f'./captcha/{capcha}.png')

                    with open(f'./captcha/{capcha}.png', 'rb') as image_file:
                        img_data = pybase64.b64encode(image_file.read()).decode("utf-8")

                    captcha_text = await crop_captcha_ocr(session, img_data)
                    await page.fill(input_selector, input_value)
                    await page.fill(captcha_selector, captcha_text)
                    # await page.wait_for_timeout(200)            
                    await page.click(submit_selector)

                    error_element = await page.query_selector('//*[@id="module3Content"]/div/p')
                    if error_element:
                        error_text = await error_element.text_content()
                        if 'Vui lòng nhập đúng mã xác nhận!' in error_text:
                            attempt_count += 1
                        else:
                            break
                    else:
                        break

            if attempt_count > 3:
                await browser.close()
                return {"status": "error", "message": "Sai mã xác nhận quá nhiều lần"}

            try:
                result_message = await page.text_content('//*[@id="module3Content"]/div/table/tbody/tr[2]/td')
                if 'Không tìm thấy kết quả.' in result_message:
                    await browser.close()
                    return {"data": "Không tìm thấy kết quả."}

                table = await page.query_selector(".ta_border")
                rows = await table.query_selector_all("tr:nth-child(n+2):not([style*='background:none'])")

                data = []
                for row in rows:
                    cols = await row.query_selector_all("td")
                    if len(cols) == 7:
                        record = {
                            "STT": (await cols[0].text_content()).replace('\n','').replace('\t',''),
                            "Mã số thuế": (await cols[1].text_content()).replace('\n','').replace('\t',''),
                            "Tên người nộp thuế": (await cols[2].text_content()).strip().replace('\n','').replace('\t',''),
                            "Cơ quan thuế": (await cols[3].text_content()).replace('\n','').replace('\t',''),
                            "CMT/Thẻ căn cước": (await cols[4].text_content()).replace('\n','').replace('\t',''),
                            "Ngày thay đổi thông tin gần nhất": (await cols[5].text_content()).replace('\n','').replace('\t',''),
                            "Ghi chú": (await cols[6].text_content()).replace('\n','').replace('\t',''),
                        }
                        data.append(record)

                await browser.close()
                return {"data": data}
            except Exception as e:
                await browser.close()
                return {"status": "error", "message": str(e)}

@app.post("/scrape_mst_bulk_from_cccd")
async def scrape_mst_bulk_from_cccd(cccd_list: CCCDList):
    sem = asyncio.Semaphore(3)  # Số lượng tác vụ đồng thời tối đa
    tasks = [asyncio.create_task(process_single_request(cccd, sem, 'cccd')) for cccd in cccd_list.cccd_list]
    results = await asyncio.gather(*tasks)

    response_list = []
    for cccd, result in zip(cccd_list.cccd_list, results):
        response_list.append({"cccd": cccd, "result": result})

    return response_list

@app.post("/scrape_mst_bulk_from_mst")
async def scrape_mst_bulk_from_mst(mst_list: MSTList):
    sem = asyncio.Semaphore(3)  # Số lượng tác vụ đồng thời tối đa
    tasks = [asyncio.create_task(process_single_request(mst, sem, 'mst')) for mst in mst_list.mst_list]
    results = await asyncio.gather(*tasks)

    response_list = []
    for mst, result in zip(mst_list.mst_list, results):
        response_list.append({"mst": mst, "result": result})

    return response_list

