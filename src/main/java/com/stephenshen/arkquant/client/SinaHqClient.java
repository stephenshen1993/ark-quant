package com.stephenshen.arkquant.client;

import lombok.Data;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 新浪行情客户端
 *
 * @author stephenshen
 * @date 2024/9/1 12:02:11
 */
@Component
public class SinaHqClient {

    @Autowired
    private RestTemplate restTemplate;

    /**
     * 获取行情数据映射
     * @param symbols
     * @return
     */
    public Map<String, HqData> hqDataMap(Collection<String> symbols) {
        // 发送请求，获取行情数据
        String hqStr = fetchHqStr(String.join(",", symbols));
        // 解析行情数据
        return parseHqDataMap(hqStr);
    }

    /**
     * 获取新浪行情
     * @param symbol 标的代码，多个使用英文逗号分隔
     * @return 行情信息串，需要单独解析
     */
    private String fetchHqStr(String symbol) {
        // 创建 HttpHeader 实例，并添加请求头
        HttpHeaders headers = new HttpHeaders();
        headers.add("Referer", "https://finance.sina.com.cn");

        // 创建 HttpEntity 实例，包含请求头和空的请求体（GET请求通常没有请求体）
        HttpEntity<String> httpEntity = new HttpEntity<>(headers);

        // 定义请求的 URL
        String url = "https://hq.sinajs.cn/list=" + symbol;

        // 发送 GET 请求，并接收响应
        ResponseEntity<String> response = restTemplate.exchange(url, HttpMethod.GET, httpEntity, String.class);
        if (response.getStatusCode().isError()) {
            throw new RuntimeException("Failed to fetch hq data: " + response.getStatusCode());
        }
        return response.getBody();
    }

    /**
     * 解析行情数据映射
     * @param hqStr
     * @return
     */
    private Map<String, HqData> parseHqDataMap(String hqStr) {
        // 提取标的信息
        Map<String, HqData> hqDataMap = new HashMap<>();

        // 正则表达式，用于匹配字符串中的所有行情数据
        String regex = "var hq_str_(sz|sh)(\\d{6})=\"(.*?)\"";
        Pattern pattern = Pattern.compile(regex);
        Matcher matcher = pattern.matcher(hqStr);

        while (matcher.find()) {
            String sinaSymbol = matcher.group(1) + matcher.group(2); // 提取标的代码
            String hqDataStr = matcher.group(3); // 获取匹配的行情数据
            hqDataMap.put(sinaSymbol, parseHqData(hqDataStr));
        }

        return hqDataMap;
    }

    /**
     * 解析行情数据
     * @param hqDataStr
     * @return
     */
    private HqData parseHqData(String hqDataStr) {
        String[] hqDataArr = hqDataStr.split(",");
        HqData hqData = new HqData();
        hqData.setName(hqDataArr[0]);
        hqData.setOpen(Double.parseDouble(hqDataArr[1]));
        hqData.setPreClose(Double.parseDouble(hqDataArr[2]));
        hqData.setCurrent(Double.parseDouble(hqDataArr[3]));
        hqData.setHigh(Double.parseDouble(hqDataArr[4]));
        hqData.setLow(Double.parseDouble(hqDataArr[5]));
        hqData.setVolume(Long.parseLong(hqDataArr[8]));
        hqData.setAmount(Double.parseDouble(hqDataArr[9]));

        hqData.setBidVolume1(Long.parseLong(hqDataArr[10]));
        hqData.setBidPrice1(Double.parseDouble(hqDataArr[11]));
        hqData.setAskVolume1(Long.parseLong(hqDataArr[12]));
        hqData.setAskPrice1(Double.parseDouble(hqDataArr[13]));
        hqData.setBidVolume2(Long.parseLong(hqDataArr[14]));
        hqData.setBidPrice2(Double.parseDouble(hqDataArr[15]));
        hqData.setAskVolume2(Long.parseLong(hqDataArr[16]));
        hqData.setAskPrice2(Double.parseDouble(hqDataArr[17]));
        hqData.setBidVolume3(Long.parseLong(hqDataArr[18]));
        hqData.setBidPrice3(Double.parseDouble(hqDataArr[19]));
        hqData.setAskVolume3(Long.parseLong(hqDataArr[20]));
        hqData.setAskPrice3(Double.parseDouble(hqDataArr[21]));
        hqData.setBidVolume4(Long.parseLong(hqDataArr[22]));
        hqData.setBidPrice4(Double.parseDouble(hqDataArr[23]));
        hqData.setAskVolume4(Long.parseLong(hqDataArr[24]));
        hqData.setAskPrice4(Double.parseDouble(hqDataArr[25]));
        hqData.setBidVolume5(Long.parseLong(hqDataArr[26]));
        hqData.setBidPrice5(Double.parseDouble(hqDataArr[27]));
        hqData.setAskVolume5(Long.parseLong(hqDataArr[28]));
        hqData.setAskPrice5(Double.parseDouble(hqDataArr[29]));
        hqData.setDate(hqDataArr[30]);
        hqData.setTime(hqDataArr[31]);
        return hqData;
    }

    /**
     * 行情数据
     */
    @Data
    public static class HqData {
        private String name; // 股票名称
        private double open; // 今日开盘价
        private double high; // 今日最高价
        private double low; // 今日最低价
        private double preClose; // 昨日收盘价
        private double current; // 当前价格
        private long volume; // 成交量（股/张）
        private double amount; // 成交金额（元）
        private double bidPrice1; // 买入价1
        private long bidVolume1; // 买入量1（股/张）
        private double askPrice1; // 卖出价1
        private long askVolume1; // 卖出量1（股/张）
        private double bidPrice2; // 买入价2
        private long bidVolume2; // 买入量2（股/张）
        private double askPrice2; // 卖出价2
        private long askVolume2; // 卖出量2（股/张）
        private double bidPrice3; // 买入价3
        private long bidVolume3; // 买入量3（股/张）
        private double askPrice3; // 卖出价3
        private long askVolume3; // 卖出量3（股/张）
        private double bidPrice4; // 买入价4
        private long bidVolume4; // 买入量4（股/张）
        private double askPrice4; // 卖出价4
        private long askVolume4; // 卖出量4（股/张）
        private double bidPrice5; // 买入价5
        private long bidVolume5; // 买入量5（股/张）
        private double askPrice5; // 卖出价5
        private long askVolume5; // 卖出量5（股/张）
        private String date; // 日期
        private String time; // 时间
    }
}
