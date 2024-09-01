package com.stephenshen.arkquant.strategy;

import com.alibaba.fastjson.JSON;
import com.google.common.collect.ImmutableList;
import com.google.common.collect.ImmutableMap;
import com.stephenshen.arkquant.entity.Account;
import com.stephenshen.arkquant.entity.Position;
import com.stephenshen.arkquant.entity.TradingPlan;
import com.stephenshen.arkquant.strategy.impl.SecurityPoolStrategy;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import java.util.List;
import java.util.Map;

/**
 * @author stephenshen
 * @date 2024/9/1 18:55:28
 */
@SpringBootTest
public class SecurityPoolStrategyTest {

    @Autowired
    private SecurityPoolStrategy securityPoolStrategy;

    @Test
    public void testMakeTradingPlan() {
        // 模拟标的池
        Map<String, String> targetSymbolMap = ImmutableMap.<String, String>builder()
                .put("123103", "震安转债")
                .put("123087", "明电转债")
                .put("123089", "九洲转2")
                .put("123056", "雪榕转债")
                .put("127019", "国城转债")
                .build();
        System.out.println("targetSymbolMap => " + JSON.toJSONString(targetSymbolMap));

        // 模拟账户
        double balance = Math.random() * 1000;
        List<Position> positions = ImmutableList.<Position>builder()
                .add(new Position("123103", "震安转债", 120.386, 50))
                .add(new Position("123087", "明电转债", 117.459, 50))
                .add(new Position("110074", "精达转债", 146.670, 40))
                .add(new Position("123089", "九洲转2", 116.450, 50))
                .add(new Position("127051", "博杰转债", 116.340, 50))
                .build();
        Account account = new Account(balance, positions);
        System.out.println("account => " + JSON.toJSONString(account));

        // 创建交易策略
        SecurityPoolStrategy.Context context = new SecurityPoolStrategy.Context(account, targetSymbolMap.keySet());
        TradingPlan tradingPlan = securityPoolStrategy.makeTradingPlan(context);
        System.out.println("tradingPlan => " + JSON.toJSONString(tradingPlan));
    }
}
