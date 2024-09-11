package com.stephenshen.arkquant.strategy;

import com.alibaba.fastjson.JSON;
import com.google.common.collect.ImmutableList;
import com.google.common.collect.ImmutableMap;
import com.stephenshen.arkquant.entity.Account;
import com.stephenshen.arkquant.entity.Position;
import com.stephenshen.arkquant.entity.TradingPlan;
import com.stephenshen.arkquant.repository.SecurityRepository;
import com.stephenshen.arkquant.strategy.impl.SecurityPoolStrategy;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import java.util.Collections;
import java.util.Comparator;
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
    @Autowired
    private SecurityRepository securityRepository;

    @Test
    public void testMakeTradingPlan() {
        // 模拟标的池
        Map<String, String> targetSymbolMap = ImmutableMap.<String, String>builder()
                .put("600455","博通股份")
                .put("603709","中源家居")
                .put("001211","双枪科技")
                .put("600137","浪莎股份")
                .put("002188","中天服务")
                .put("600493","凤竹纺织")
                .put("001336","楚环科技")
                .put("600448","华纺股份")
                .put("603022","新通联")
                .put("002295","精艺股份")
                .put("002830","名雕股份")
                .put("600883","博闻科技")
                .put("002853","皮阿诺")
                .put("603045","福达合金")
                .put("003017","大洋生物")
                .put("002247","聚力文化")
                .put("002883","中设股份")
                .put("003003","天元股份")
                .put("002715","登云股份")
                .put("603159","上海亚虹")
                .build();
        System.out.println("targetSymbolMap => " + JSON.toJSONString(targetSymbolMap));

        // 模拟账户
        double balance = 1253.40;
        List<Position> positions = ImmutableList.<Position>builder()
                .add(new Position("001211","双枪科技", null, 300))
                .add(new Position("600137","浪莎股份", null, 500))
                .add(new Position("603045","福达合金", null, 600))
                .add(new Position("600493","凤竹纺织", null, 1300))
                .add(new Position("002188","中天服务", null, 1400))
                .add(new Position("002295","精艺股份", null, 1100))
                .add(new Position("600883","博闻科技", null, 1000))
                .add(new Position("600448","华纺股份", null, 2700))
                .add(new Position("000856","冀东装备", null, 900))
                .add(new Position("002247","聚力文化", null, 3500))
                .add(new Position("603022","新通联", null, 900))
                .add(new Position("603709","中源家居", null, 600))
                .add(new Position("002883","中设股份", null, 600))
                .add(new Position("600455","博通股份", null, 300))
                .add(new Position("003017","大洋生物", null, 300))
                .add(new Position("002830","名雕股份", null, 600))
                .add(new Position("001336","楚环科技", null, 300))
                .add(new Position("002853","皮阿诺", null, 800))
                .add(new Position("603159","上海亚虹", null, 600))
                .add(new Position("003003","天元股份", null, 700))
                .build();
        Account account = new Account(balance, positions);
        System.out.println("account => " + JSON.toJSONString(account));

        // 创建交易策略
        SecurityPoolStrategy.Context context = new SecurityPoolStrategy.Context(account, targetSymbolMap.keySet());
        List<TradingPlan> tradingPlans = securityPoolStrategy.makeTradingPlan(context);
        tradingPlans.sort(Comparator.comparing(TradingPlan::getValue));
        System.out.println("tradingPlans => " + JSON.toJSONString(tradingPlans));
    }
}
